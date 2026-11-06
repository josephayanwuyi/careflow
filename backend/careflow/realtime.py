"""Live updates over Server-Sent Events.

Publishing: services call `publish()` inside their transaction. It issues `pg_notify`, which
PostgreSQL delivers only if the transaction commits, so clients never hear about changes
that were rolled back. This works across processes and servers sharing the database.

Delivery: each API process runs one background listener thread (`Broker`) that LISTENs on
the channel and hands events to subscribers. Every subscriber has a `Scope` built from the
caller's permissions at connect time; events outside the scope are never delivered.

Payloads carry identifiers and statuses only - never names, phone numbers or reasons.
Clients treat an event as "something changed" and re-fetch the resource over the REST API,
which applies full authorisation again.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

import psycopg
from sqlalchemy import text
from sqlalchemy.engine import make_url

from careflow.extensions import db

CHANNEL = "careflow_events"
log = logging.getLogger("careflow.realtime")


def publish(event_type: str, *, hospital_id: uuid.UUID, action: str | None = None,
            queue_id: uuid.UUID | None = None, entry_id: uuid.UUID | None = None,
            appointment_id: uuid.UUID | None = None, doctor_id: uuid.UUID | None = None,
            patient_id: uuid.UUID | None = None, status: str | None = None) -> None:
    payload = {
        "id": uuid.uuid4().hex,
        "type": event_type,
        "action": action,
        "hospital_id": str(hospital_id),
        "queue_id": str(queue_id) if queue_id else None,
        "entry_id": str(entry_id) if entry_id else None,
        "appointment_id": str(appointment_id) if appointment_id else None,
        "doctor_id": str(doctor_id) if doctor_id else None,
        "patient_id": str(patient_id) if patient_id else None,  # used for routing; stripped before sending
        "status": status,
    }
    db.session.execute(text("SELECT pg_notify(:channel, :payload)"),
                       {"channel": CHANNEL, "payload": json.dumps(payload)})


# --- Subscription scope ------------------------------------------------------------------


@dataclass
class Scope:
    """What one connected client may hear about."""

    staff_hospitals: set[str] = field(default_factory=set)   # all queue/appointment events in these hospitals
    doctor_ids: set[str] = field(default_factory=set)          # events about these doctors' queues/appointments
    patient_id: str | None = None                              # the patient's own appointments and entries
    patient_queue_ids: set[str] = field(default_factory=set)   # queues the patient is in (position changes)
    patient_doctor_ids: set[str] = field(default_factory=set)  # doctors of those queues (availability changes)

    def filter(self, event: dict) -> dict | None:
        """Return the message to send, or None if the client must not receive this event."""
        public = {k: v for k, v in event.items() if k != "patient_id"}
        if event.get("hospital_id") in self.staff_hospitals:
            return public
        if event.get("doctor_id") and event["doctor_id"] in self.doctor_ids:
            return public
        if self.patient_id:
            if event.get("patient_id") == self.patient_id:
                if event.get("queue_id"):
                    self.patient_queue_ids.add(event["queue_id"])
                if event.get("doctor_id"):
                    self.patient_doctor_ids.add(event["doctor_id"])
                return public
            if event.get("queue_id") in self.patient_queue_ids:
                # Someone else's change in my queue: say only that the queue changed.
                return {"id": event["id"], "type": "queue_changed", "queue_id": event["queue_id"]}
            if event.get("type") == "doctor_status" and event.get("doctor_id") in self.patient_doctor_ids:
                return {"id": event["id"], "type": "doctor_status", "doctor_id": event["doctor_id"],
                        "status": event.get("status")}
        return None


class Subscriber:
    def __init__(self, scope: Scope, maxsize: int = 200):
        self.scope = scope
        self.messages: queue.Queue = queue.Queue(maxsize=maxsize)
        self.overflowed = False

    def offer(self, event: dict) -> None:
        message = self.scope.filter(event)
        if message is None:
            return
        try:
            self.messages.put_nowait(message)
        except queue.Full:
            self.overflowed = True  # the stream tells the client to resync


# --- Broker ------------------------------------------------------------------------------


class Broker:
    """One LISTEN connection per process, shared by all subscribers."""

    def __init__(self):
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._dsn: str | None = None
        self.ready = threading.Event()

    def configure(self, sqlalchemy_url: str) -> None:
        self._dsn = make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)

    def subscribe(self, scope: Scope) -> Subscriber:
        sub = Subscriber(scope)
        with self._lock:
            self._subscribers.add(sub)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="careflow-sse-listener", daemon=True)
                self._thread.start()
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def _dispatch(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("realtime: ignoring malformed notification")
            return
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub.offer(event)

    def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                with psycopg.connect(self._dsn, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.ready.set()
                    backoff = 1.0
                    while True:
                        for notify in conn.notifies(timeout=5.0):
                            self._dispatch(notify.payload)
                        with self._lock:
                            if not self._subscribers:
                                self.ready.clear()
                                self._thread = None
                                return
            except psycopg.Error:
                self.ready.clear()
                log.exception("realtime: listener connection failed; retrying in %.0fs", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


broker = Broker()
