"""Thread-safe client for the CareFlow REST API.

- Every request has a timeout; network failures become `NetworkError` with a clear message.
- Access tokens are refreshed automatically once when they expire; if refreshing fails the
  session is over (`SessionExpired`) and `on_session_expired` is called.
- Tokens are held in memory only and are never written to disk or logs.
- The client never decides permissions: the server is authoritative and its errors are shown.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import httpx

from careflow_desktop import __version__
from careflow_desktop.api.errors import ApiError, NetworkError, SessionExpired

log = logging.getLogger("careflow.desktop.api")

_REFRESHABLE = {"token_expired"}


class ApiClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            headers={"User-Agent": f"CareFlowDesktop/{__version__}", "Accept": "application/json"},
            transport=transport,
        )
        self._lock = threading.Lock()
        self._access: str | None = None
        self._refresh: str | None = None
        self.on_session_expired: Callable[[], None] | None = None

    # --- Session -------------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return self._access is not None

    @property
    def access_token(self) -> str | None:
        return self._access

    def login(self, email: str, password: str) -> dict:
        data = self._send("POST", "/auth/login",
                          json={"email": email, "password": password, "client_type": "desktop"}, auth=False)
        with self._lock:
            self._access, self._refresh = data["access_token"], data["refresh_token"]
        return data["user"]

    def logout(self) -> None:
        """End the session on the server (best effort) and forget the tokens."""
        try:
            if self._access:
                self._send("POST", "/auth/logout")
        except ApiError as exc:
            log.info("logout request failed: %s", exc.code)
        finally:
            with self._lock:
                self._access = self._refresh = None

    def ensure_fresh(self) -> None:
        """Refresh the access token now (used by the live-update stream after a 401)."""
        self._refresh_tokens(self._access)

    def _expire(self) -> None:
        with self._lock:
            was_authenticated = self._access is not None
            self._access = self._refresh = None
        if was_authenticated and self.on_session_expired:
            self.on_session_expired()

    def _refresh_tokens(self, token_used: str | None) -> None:
        with self._lock:
            if self._access != token_used:
                return  # another thread refreshed already
            refresh = self._refresh
        if not refresh:
            self._expire()
            raise SessionExpired()
        try:
            data = self._send("POST", "/auth/refresh", json={"refresh_token": refresh}, auth=False)
        except ApiError as exc:
            if isinstance(exc, NetworkError):
                raise
            self._expire()
            raise SessionExpired() from None
        with self._lock:
            self._access, self._refresh = data["access_token"], data["refresh_token"]

    # --- Transport -----------------------------------------------------------------------

    def _send(self, method: str, path: str, *, json: Any = None, params: dict | None = None, auth: bool = True,
              raw: bool = False) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""} or None
        for attempt in (1, 2):
            token = self._access
            headers = {"Authorization": f"Bearer {token}"} if auth and token else {}
            if auth and not token:
                raise SessionExpired()
            try:
                resp = self._http.request(method, path, json=json, params=params, headers=headers)
            except httpx.TimeoutException:
                raise NetworkError("The CareFlow server took too long to respond. Please try again.") from None
            except httpx.TransportError:
                raise NetworkError("Cannot reach the CareFlow server. Check the network connection.") from None

            if resp.status_code == 401 and auth:
                code = _error_code(resp)
                if attempt == 1 and code in _REFRESHABLE:
                    self._refresh_tokens(token)
                    continue
                self._expire()
                raise SessionExpired()
            if resp.is_error:
                raise _to_error(resp)
            if raw:
                return resp.content
            return resp.json() if resp.content else None
        raise SessionExpired()  # pragma: no cover - loop always returns or raises

    def get(self, path: str, **params) -> Any:
        return self._send("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._send("POST", path, json=body if body is not None else {})

    def get_raw(self, path: str, **params) -> bytes:
        return self._send("GET", path, params=params, raw=True)

    def stream(self, path: str, timeout: httpx.Timeout):
        """Open a streaming GET (for Server-Sent Events). Caller must close the response."""
        token = self._access
        if not token:
            raise SessionExpired()
        request = self._http.build_request("GET", path, headers={"Authorization": f"Bearer {token}",
                                                                 "Accept": "text/event-stream"}, timeout=timeout)
        return self._http.send(request, stream=True)

    def close(self) -> None:
        self._http.close()

    # --- Endpoints used by the desktop app ---------------------------------------------------

    def me(self) -> dict:
        return self.get("/auth/me")

    def dashboard(self, hid) -> dict:
        return self.get(f"/hospitals/{hid}/dashboard")

    def doctors(self, hid) -> list[dict]:
        return self.get(f"/hospitals/{hid}/doctors")

    def departments(self, hid) -> list[dict]:
        return self.get(f"/hospitals/{hid}/departments")

    def slots(self, doctor_id, day: str) -> dict:
        return self.get(f"/doctors/{doctor_id}/slots", date=day)

    # Appointments
    def appointments(self, hid, **filters) -> dict:
        if isinstance(filters.get("status"), (list, tuple)):
            filters["status"] = ",".join(filters["status"])
        return self.get(f"/hospitals/{hid}/appointments", **filters)

    def appointment(self, appointment_id) -> dict:
        return self.get(f"/appointments/{appointment_id}")

    def appointment_history(self, appointment_id) -> list[dict]:
        return self.get(f"/appointments/{appointment_id}/history")

    def appointment_action(self, appointment_id, action: str, **body) -> dict:
        return self.post(f"/appointments/{appointment_id}/{action}", {k: v for k, v in body.items() if v is not None})

    def check_in(self, appointment_id) -> dict:
        return self.post(f"/appointments/{appointment_id}/check-in")

    def book_appointment(self, hid, *, patient_id, doctor_id, start_at: str, reason_for_visit: str | None) -> dict:
        return self.post(f"/hospitals/{hid}/appointments", {"patient_id": patient_id, "doctor_id": doctor_id,
                                                            "start_at": start_at, "reason_for_visit": reason_for_visit})

    # Queue
    def queues(self, hid, day: str | None = None) -> list[dict]:
        return self.get(f"/hospitals/{hid}/queues", date=day)

    def queue_entries(self, queue_id, include_finished: bool = False) -> list[dict]:
        return self.get(f"/queues/{queue_id}/entries", include_finished=str(include_finished).lower())

    def queue_entry_events(self, entry_id) -> list[dict]:
        return self.get(f"/queue-entries/{entry_id}/events")

    def call_next(self, queue_id) -> dict:
        return self.post(f"/queues/{queue_id}/call-next")

    def set_queue_paused(self, queue_id, paused: bool, reason: str | None = None) -> dict:
        return self.post(f"/queues/{queue_id}/{'pause' if paused else 'resume'}",
                         {"reason": reason} if paused else {})

    def entry_action(self, entry_id, action: str, **body) -> dict:
        return self.post(f"/queue-entries/{entry_id}/{action}", {k: v for k, v in body.items() if v is not None})

    def add_walk_in(self, hid, *, patient_id, doctor_id) -> dict:
        return self.post(f"/hospitals/{hid}/queue/walk-ins", {"patient_id": patient_id, "doctor_id": doctor_id})

    # Patients
    def search_patients(self, hid, query: str) -> list[dict]:
        return self.get(f"/hospitals/{hid}/patients", q=query)

    def register_patient(self, hid, **data) -> dict:
        return self.post(f"/hospitals/{hid}/patients", {k: v for k, v in data.items() if v not in (None, "")})

    # Doctors
    def doctor_availability(self, hid) -> list[dict]:
        return self.get(f"/hospitals/{hid}/doctors/availability")

    def verify_arrival(self, hid, doctor_id, note: str | None = None) -> dict:
        return self.post(f"/hospitals/{hid}/doctors/{doctor_id}/verify-arrival", {"note": note})

    # Audit and reports
    def audit_events(self, hid, **filters) -> dict:
        return self.get(f"/hospitals/{hid}/audit-events", **filters)

    def export_audit_csv(self, hid, **filters) -> bytes:
        return self.get_raw(f"/hospitals/{hid}/audit-events/export.csv", **filters)

    def export_queue_day_csv(self, hid, day: str) -> bytes:
        return self.get_raw(f"/hospitals/{hid}/reports/queue-day.csv", date=day)

    # Admin, Staff & Notifications
    def list_staff(self, hid) -> list[dict]:
        return self.get(f"/hospitals/{hid}/staff")

    def create_staff(self, hid, **data) -> dict:
        payload = {k: v for k, v in data.items() if v not in (None, "")}
        return self.post(f"/hospitals/{hid}/staff", payload)

    def create_department(self, hid, **data) -> dict:
        return self.post(f"/hospitals/{hid}/departments", data)

    def notification_report(self, hid) -> dict:
        return self.get(f"/hospitals/{hid}/notifications/report")


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def _to_error(resp: httpx.Response) -> ApiError:
    try:
        err = resp.json()["error"]
        return ApiError(resp.status_code, err.get("code") or "error", err.get("message") or "The request failed.",
                        err.get("details"), err.get("request_id"))
    except (ValueError, KeyError, TypeError):
        if resp.status_code >= 500:
            return ApiError(resp.status_code, "server_error", "The CareFlow server had a problem. Please try again.")
        return ApiError(resp.status_code, "error", f"The request failed (HTTP {resp.status_code}).")
