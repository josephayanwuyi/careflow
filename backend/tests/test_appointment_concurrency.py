"""Concurrent appointment operations against real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from tests.conftest import login, register_patient, slot_time

WORKERS = 8


def _token(app, email):
    return {"Authorization": f"Bearer {login(app.test_client(), email)['access_token']}"}


def test_concurrent_approvals_of_same_slot_allow_exactly_one(app, factory):
    clinic = factory.clinic()
    client = app.test_client()
    appt_ids = []
    for _ in range(WORKERS):
        p = register_patient(client)
        resp = client.post("/api/v1/appointments", headers=_token(app, p["email"]),
                           json={"doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()})
        appt_ids.append(resp.get_json()["id"])
    staff = _token(app, clinic["receptionist"]["email"])
    barrier = Barrier(WORKERS)

    def approve(appt_id):
        c = app.test_client()
        barrier.wait()
        return c.post(f"/api/v1/appointments/{appt_id}/approve", json={}, headers=staff)

    with ThreadPoolExecutor(WORKERS) as pool:
        responses = list(pool.map(approve, appt_ids))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] + [409] * (WORKERS - 1)
    assert all(r.get_json()["error"]["code"] == "slot_unavailable" for r in responses if r.status_code == 409)


def test_concurrent_conflicting_actions_on_one_appointment(app, factory):
    clinic = factory.clinic()
    client = app.test_client()
    p = register_patient(client)
    appt = client.post("/api/v1/appointments", headers=_token(app, p["email"]),
                       json={"doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()}).get_json()
    staff = _token(app, clinic["receptionist"]["email"])
    barrier = Barrier(2)

    def run(action):
        c = app.test_client()
        barrier.wait()
        body = {"reason": "Not possible"} if action == "reject" else {}
        return c.post(f"/api/v1/appointments/{appt['id']}/{action}", json=body, headers=staff).status_code

    with ThreadPoolExecutor(2) as pool:
        statuses = sorted(pool.map(run, ["approve", "reject"]))
    assert statuses == [200, 409]
    history = client.get(f"/api/v1/appointments/{appt['id']}/history", headers=staff).get_json()
    assert len(history) == 2  # requested + exactly one decision


def test_concurrent_idempotent_submissions_create_one_request(app, factory):
    clinic = factory.clinic()
    client = app.test_client()
    p = register_patient(client)
    headers = {**_token(app, p["email"]), "Idempotency-Key": "double-tap-0001"}
    body = {"doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()}
    barrier = Barrier(WORKERS)

    def submit(_):
        c = app.test_client()
        barrier.wait()
        return c.post("/api/v1/appointments", json=body, headers=headers)

    with ThreadPoolExecutor(WORKERS) as pool:
        responses = list(pool.map(submit, range(WORKERS)))
    assert all(r.status_code in (200, 201) for r in responses), [r.get_json() for r in responses]
    assert len({r.get_json()["id"] for r in responses}) == 1
    assert sum(r.status_code == 201 for r in responses) == 1
