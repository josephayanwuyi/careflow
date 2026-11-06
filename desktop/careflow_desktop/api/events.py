"""Live updates: a background thread that reads the server's Server-Sent Events stream.

Emits `event_received(dict)` for each event and `state_changed(str)` with one of
"connecting", "live", "offline". Reconnects with exponential backoff. Events are hints:
views reload data from the API when they receive one.
"""

from __future__ import annotations

import json
import logging
import threading

import httpx
from PySide6.QtCore import QThread, Signal

from careflow_desktop.api.client import ApiClient
from careflow_desktop.api.errors import ApiError, SessionExpired

log = logging.getLogger("careflow.desktop.events")

STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=45.0, write=5.0, pool=5.0)  # server heartbeats every 15 s


def parse_sse(lines):
    """Yield (event, data) from an iterator of SSE text lines."""
    event, data = "message", []
    for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue  # comment / keepalive
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


class EventStream(QThread):
    event_received = Signal(dict)
    state_changed = Signal(str)

    def __init__(self, client: ApiClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._stop = threading.Event()
        self._response: httpx.Response | None = None
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._response is not None:
                try:
                    self._response.close()  # unblocks the reader
                except Exception:  # noqa: BLE001 - closing a broken socket may raise anything
                    log.debug("error closing event stream", exc_info=True)

    def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self.state_changed.emit("connecting")
            try:
                response = self._client.stream("/stream", STREAM_TIMEOUT)
                with self._lock:
                    self._response = response
                try:
                    if response.status_code == 401:
                        self._client.ensure_fresh()  # raises SessionExpired if the session is over
                        continue
                    response.raise_for_status()
                    for event, data in parse_sse(response.iter_lines()):
                        if event == "ready":
                            self.state_changed.emit("live")
                            backoff = 1.0
                            continue
                        if event == "session_ended":
                            self._client.ensure_fresh()
                            break
                        try:
                            payload = json.loads(data) if data else {}
                        except json.JSONDecodeError:
                            log.warning("ignoring malformed event data")
                            continue
                        payload.setdefault("type", event)
                        self.event_received.emit(payload)
                finally:
                    with self._lock:
                        self._response = None
                    response.close()
            except SessionExpired:
                self.state_changed.emit("offline")
                return  # the app returns to the login screen
            except (httpx.HTTPError, ApiError) as exc:
                if self._stop.is_set():
                    break
                log.info("event stream interrupted: %s", type(exc).__name__)
            self.state_changed.emit("offline")
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)
        self.state_changed.emit("offline")
