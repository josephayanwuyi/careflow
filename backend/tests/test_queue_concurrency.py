"""Concurrent queue operations: these rely on real PostgreSQL locking and constraints."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from threading import Barrier

import pytest

from tests.conftest import login, register_patient

WORKERS = 8


def _h(app, email):
    return {"Authorization": f"Bearer {login(app.test_client(), email)['access_token']}"}


@pytest.fixture
def clinic(factory):
    hid = factory.hospital("HOSA")
    dept = factory.department(hid)
    doctor = factory.staff(hid, "doctor", department_id=dept)
    factory.schedule(hid, doctor["doctor_id"], start=time(0, 0), end=time(23, 45))
    return {"hospital_id": hid, "doctor": doctor, "receptionist": factory.staff(hid, "receptionist")}


def _appointments(app, factory, clinic, n):
    client = app.test_client()
    base = datetime.now(UTC).replace(second=0, microsecond=0)
    out = []
    for i in range(n):
        p = register_patient(client)
        appt = factory.approved_appointment(clinic["hospital_id"], clinic["doctor"]["doctor_id"], p["patient_id"],
                                            start_at=base + timedelta(minutes=i))
        out.append((p, appt))
    return out


def _run_concurrently(fn, args):
    barrier = Barrier(len(args))

    def wrapped(a):
        barrier.wait()
        return fn(a)

    with ThreadPoolExecutor(len(args)) as pool:
        return list(pool.map(wrapped, args))


def test_concurrent_check_ins_get_unique_tickets(app, factory, clinic):
    pairs = _appointments(app, factory, clinic, WORKERS)
    headers = [(appt, _h(app, p["email"])) for p, appt in pairs]
    responses = _run_concurrently(
        lambda a: app.test_client().post(f"/api/v1/appointments/{a[0]}/check-in", headers=a[1]), headers)
    assert [r.status_code for r in responses] == [201] * WORKERS, [r.get_json() for r in responses]
    assert sorted(r.get_json()["ticket_number"] for r in responses) == list(range(1, WORKERS + 1))
    assert len({r.get_json()["queue_id"] for r in responses}) == 1  # one queue created despite the race


def test_double_check_in_of_same_appointment(app, factory, clinic):
    [(p, appt)] = _appointments(app, factory, clinic, 1)
    h = _h(app, p["email"])
    responses = _run_concurrently(
        lambda _: app.test_client().post(f"/api/v1/appointments/{appt}/check-in", headers=h), range(WORKERS))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201] + [409] * (WORKERS - 1)


def test_concurrent_call_next_calls_exactly_one_patient(app, factory, clinic):
    pairs = _appointments(app, factory, clinic, 3)
    staff = _h(app, clinic["receptionist"]["email"])
    client = app.test_client()
    queue_id = None
    for _, appt in pairs:
        queue_id = client.post(f"/api/v1/appointments/{appt}/check-in", headers=staff).get_json()["queue_id"]
    doc = _h(app, clinic["doctor"]["email"])
    for status in ("checked_in", "available"):
        client.post("/api/v1/doctors/me/status", json={"status": status}, headers=doc)

    callers = [doc, staff] * (WORKERS // 2)
    responses = _run_concurrently(
        lambda h: app.test_client().post(f"/api/v1/queues/{queue_id}/call-next", headers=h), callers)
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] + [409] * (WORKERS - 1)
    entries = client.get(f"/api/v1/queues/{queue_id}/entries", headers=staff).get_json()
    assert [e["status"] for e in entries].count("called") == 1


def test_concurrent_conflicting_entry_actions(app, factory, clinic):
    [(p, appt)] = _appointments(app, factory, clinic, 1)
    staff = _h(app, clinic["receptionist"]["email"])
    client = app.test_client()
    entry = client.post(f"/api/v1/appointments/{appt}/check-in", headers=staff).get_json()
    patient_h = _h(app, p["email"])

    def act(who):
        c = app.test_client()
        if who == "staff":
            return c.post(f"/api/v1/queue-entries/{entry['id']}/cancel", json={"reason": "Desk cancel"}, headers=staff)
        return c.post(f"/api/v1/queue-entries/{entry['id']}/cancel", json={}, headers=patient_h)

    statuses = sorted(r.status_code for r in _run_concurrently(act, ["staff", "patient"]))
    assert statuses == [200, 409]
    events = client.get(f"/api/v1/queue-entries/{entry['id']}/events", headers=staff).get_json()
    assert [e["event_type"] for e in events] == ["checked_in", "cancelled"]
