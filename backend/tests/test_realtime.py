"""Live updates: scope filtering (unit) and pg_notify delivery through the broker (integration)."""

import queue as std_queue
import uuid
from datetime import UTC, datetime, time

import pytest

from careflow.extensions import db
from careflow.realtime import Scope, broker, publish
from tests.conftest import auth_headers, register_patient

H1, H2 = str(uuid.uuid4()), str(uuid.uuid4())
Q1, D1, P1, P2 = (str(uuid.uuid4()) for _ in range(4))


def _event(**kw):
    base = {"id": "e1", "type": "queue_entry", "action": "called", "hospital_id": H1, "queue_id": Q1,
            "entry_id": "x", "appointment_id": None, "doctor_id": D1, "patient_id": P1, "status": "called"}
    base.update(kw)
    return base


def test_staff_scope_gets_hospital_events_without_patient_id():
    msg = Scope(staff_hospitals={H1}).filter(_event())
    assert msg["entry_id"] == "x" and "patient_id" not in msg
    assert Scope(staff_hospitals={H2}).filter(_event()) is None


def test_doctor_scope_gets_only_own_doctor_events():
    assert Scope(doctor_ids={D1}).filter(_event()) is not None
    assert Scope(doctor_ids={str(uuid.uuid4())}).filter(_event()) is None


def test_patient_scope():
    own = Scope(patient_id=P1)
    assert own.filter(_event())["entry_id"] == "x"
    # Learned the queue from its own event: now hears that the queue changed, without details.
    other = own.filter(_event(patient_id=P2, entry_id="y"))
    assert other == {"id": "e1", "type": "queue_changed", "queue_id": Q1}
    assert own.filter(_event(type="doctor_status", queue_id=None, patient_id=None, status="on_break")) == \
        {"id": "e1", "type": "doctor_status", "doctor_id": D1, "status": "on_break"}
    stranger = Scope(patient_id=P2)
    assert stranger.filter(_event(queue_id=str(uuid.uuid4()))) is None


@pytest.fixture
def clinic(factory):
    hid = factory.hospital("HOSA")
    dept = factory.department(hid)
    doctor = factory.staff(hid, "doctor", department_id=dept)
    factory.schedule(hid, doctor["doctor_id"], start=time(0, 0), end=time(23, 45))
    return {"hospital_id": hid, "doctor": doctor, "receptionist": factory.staff(hid, "receptionist")}


def _drain(sub, timeout=5.0):
    messages = []
    try:
        messages.append(sub.messages.get(timeout=timeout))
        while True:
            messages.append(sub.messages.get(timeout=0.5))
    except std_queue.Empty:
        pass
    return messages


def test_committed_changes_reach_subscribers(client, factory, clinic):
    p = register_patient(client)
    staff_sub = broker.subscribe(Scope(staff_hospitals={str(clinic["hospital_id"])}))
    other_sub = broker.subscribe(Scope(staff_hospitals={str(uuid.uuid4())}))
    patient_sub = broker.subscribe(Scope(patient_id=p["patient_id"]))
    try:
        assert broker.ready.wait(10)
        appt = factory.approved_appointment(clinic["hospital_id"], clinic["doctor"]["doctor_id"], p["patient_id"],
                                            start_at=datetime.now(UTC).replace(second=0, microsecond=0))
        resp = client.post(f"/api/v1/appointments/{appt}/check-in", headers=auth_headers(client, p["email"]))
        assert resp.status_code == 201

        staff_msgs = _drain(staff_sub)
        assert {(m["type"], m["action"]) for m in staff_msgs} >= {("queue_entry", "checked_in"),
                                                                 ("appointment", "checked_in")}
        assert all("patient_id" not in m for m in staff_msgs)
        assert {m["type"] for m in _drain(patient_sub)} >= {"queue_entry", "appointment"}
        assert _drain(other_sub, timeout=1) == []
    finally:
        for sub in (staff_sub, other_sub, patient_sub):
            broker.unsubscribe(sub)


def test_rolled_back_changes_are_not_published(app, clinic):
    sub = broker.subscribe(Scope(staff_hospitals={str(clinic["hospital_id"])}))
    try:
        assert broker.ready.wait(10)
        with app.app_context():
            publish("queue", hospital_id=clinic["hospital_id"], action="rolled_back")
            db.session.rollback()
            publish("queue", hospital_id=clinic["hospital_id"], action="committed")
            db.session.commit()
        actions = [m["action"] for m in _drain(sub)]
        assert actions == ["committed"]
    finally:
        broker.unsubscribe(sub)


def test_stream_endpoint(client, clinic):
    assert client.get("/api/v1/stream").status_code == 401
    headers = auth_headers(client, clinic["receptionist"]["email"])
    resp = client.get("/api/v1/stream", headers=headers, buffered=False)
    try:
        assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
        # Hop-by-hop headers break real WSGI servers (waitress/gunicorn), though the test client allows them.
        assert "Connection" not in resp.headers
        chunks = iter(resp.response)
        opening = b"".join(_as_bytes(next(chunks)) for _ in range(2))
        assert opening.startswith(b"retry: 5000") and b"event: ready" in opening
    finally:
        resp.close()


def _as_bytes(chunk) -> bytes:
    return chunk if isinstance(chunk, bytes) else chunk.encode()
