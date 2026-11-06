"""End-to-end checks against a real CareFlow server with seed data (skipped by default).

Run with:
    CAREFLOW_LIVE_API_URL=http://127.0.0.1:8000 CAREFLOW_LIVE_PASSWORD=<seed password> pytest tests/test_live_api.py

Uses the synthetic seed accounts (reception1@, patient3@example.test). Leaves no pending changes:
the appointment it creates is cancelled at the end.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import httpx
import pytest
from PySide6.QtCore import Qt

from careflow_desktop.api.client import ApiClient
from careflow_desktop.api.events import EventStream
from careflow_desktop.state import AppState, desk_hospitals
from careflow_desktop.ui import appointments as appointments_module
from careflow_desktop.ui.appointments import AppointmentsView

URL = os.environ.get("CAREFLOW_LIVE_API_URL")
PASSWORD = os.environ.get("CAREFLOW_LIVE_PASSWORD")
pytestmark = pytest.mark.skipif(not (URL and PASSWORD), reason="set CAREFLOW_LIVE_API_URL and CAREFLOW_LIVE_PASSWORD")


@pytest.fixture
def desk():
    client = ApiClient(URL)
    user = client.login("reception1@example.test", PASSWORD)
    state = AppState(client)
    [hospital] = desk_hospitals(user)
    hospital.timezone = client.get(f"/hospitals/{hospital.id}")["timezone"]
    state.hospital = hospital
    state.doctors = client.doctors(hospital.id)
    yield state
    client.logout()
    client.close()


@pytest.fixture
def patient_api():
    http = httpx.Client(base_url=f"{URL}/api/v1", timeout=10)
    token = http.post("/auth/login", json={"email": "patient3@example.test", "password": PASSWORD}).json()["access_token"]
    http.headers["Authorization"] = f"Bearer {token}"
    yield http
    http.close()


def _free_slot(patient_api, doctor_id):
    day = date.today() + timedelta(days=1)
    for _ in range(14):
        slots = patient_api.get(f"/doctors/{doctor_id}/slots", params={"date": day.isoformat()}).json()["slots"]
        free = [s for s in slots if s["available"]]
        if free:
            return free[-1]["start_at"]
        day += timedelta(days=1)
    pytest.skip("no free slot in the next two weeks")


def test_request_appears_live_and_is_approved_from_desktop(qtbot, desk, patient_api, monkeypatch):
    stream = EventStream(desk.client)
    states = []
    stream.state_changed.connect(states.append)
    stream.event_received.connect(desk.server_event.emit)
    stream.start()
    view = AppointmentsView(desk)
    qtbot.addWidget(view)
    view.show()
    appt_id = None
    try:
        qtbot.waitUntil(lambda: "live" in states, timeout=10_000)
        qtbot.waitUntil(lambda: view.stack.currentWidget() is not view.stack._loading, timeout=10_000)

        doctor = desk.doctors[0]
        resp = patient_api.post("/appointments", json={"doctor_id": doctor["id"],
                                                       "start_at": _free_slot(patient_api, doctor["id"])})
        assert resp.status_code == 201, resp.text
        appt_id = resp.json()["id"]

        # No manual refresh: the live update makes the request appear in the pending list.
        qtbot.waitUntil(lambda: view.table.record_model.row_of(appt_id) >= 0, timeout=10_000)
        assert view.table.select_key(appt_id)
        assert view.btn_approve.isEnabled()

        monkeypatch.setattr(appointments_module, "confirm", lambda *a, **k: True)
        qtbot.mouseClick(view.btn_approve, Qt.LeftButton)
        qtbot.waitUntil(lambda: patient_api.get(f"/appointments/{appt_id}").json()["status"] == "approved",
                        timeout=10_000)
    finally:
        stream.stop()
        stream.wait(5000)
        if appt_id:
            patient_api.post(f"/appointments/{appt_id}/cancel", json={"reason": "live test cleanup"})


def test_dashboard_and_queues_load(desk):
    data = desk.client.dashboard(desk.hospital_id)
    assert {"queue", "appointments", "doctors", "alerts"} <= set(data)
    assert isinstance(desk.client.queues(desk.hospital_id), list)
