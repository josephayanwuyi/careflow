"""Concurrency tests. These need real PostgreSQL row locking, so they use threads with
separate connections rather than a single test client."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from tests.conftest import login, register_patient

WORKERS = 8


def test_concurrent_walk_in_registrations_get_unique_numbers(app, factory):
    hid = factory.hospital("HOSA")
    rec = factory.staff(hid, "receptionist")
    token = login(app.test_client(), rec["email"])["access_token"]
    barrier = Barrier(WORKERS)

    def register(i):
        client = app.test_client()
        barrier.wait()
        return client.post(f"/api/v1/hospitals/{hid}/patients",
                           json={"full_name": f"Concurrent Patient {i}", "phone": f"+23480300001{i:02d}"},
                           headers={"Authorization": f"Bearer {token}"})

    with ThreadPoolExecutor(WORKERS) as pool:
        responses = list(pool.map(register, range(WORKERS)))
    assert [r.status_code for r in responses] == [201] * WORKERS
    numbers = {r.get_json()["hospital_patient_number"] for r in responses}
    assert numbers == {f"HOSA-{n:06d}" for n in range(1, WORKERS + 1)}


def test_concurrent_duplicate_registrations_create_one_patient(app, factory):
    hid = factory.hospital("HOSA")
    rec = factory.staff(hid, "receptionist")
    token = login(app.test_client(), rec["email"])["access_token"]
    barrier = Barrier(WORKERS)

    def register(_):
        client = app.test_client()
        barrier.wait()
        return client.post(f"/api/v1/hospitals/{hid}/patients",
                           json={"full_name": "Same Person", "phone": "+2348039999999"},
                           headers={"Authorization": f"Bearer {token}"})

    with ThreadPoolExecutor(WORKERS) as pool:
        statuses = sorted(r.status_code for r in pool.map(register, range(WORKERS)))
    assert statuses == [201] + [409] * (WORKERS - 1)


def test_concurrent_refresh_with_same_token_succeeds_at_most_once(app):
    client = app.test_client()
    p = register_patient(client)
    refresh_token = login(client, p["email"])["refresh_token"]
    barrier = Barrier(WORKERS)

    def refresh(_):
        c = app.test_client()
        barrier.wait()
        return c.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token}).status_code

    with ThreadPoolExecutor(WORKERS) as pool:
        statuses = list(pool.map(refresh, range(WORKERS)))
    assert statuses.count(200) <= 1
    assert statuses.count(200) + statuses.count(401) == WORKERS
