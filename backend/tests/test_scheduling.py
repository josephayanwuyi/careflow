from datetime import datetime, timedelta

from tests.conftest import LAGOS, auth_headers, register_patient, slot_time


def _day(days_ahead=7):
    return (datetime.now(LAGOS).date() + timedelta(days=days_ahead)).isoformat()


def _slots(client, doctor_id, day):
    resp = client.get(f"/api/v1/doctors/{doctor_id}/slots", query_string={"date": day})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_admin_manages_templates(client, factory):
    hid = factory.hospital()
    dept = factory.department(hid)
    doc = factory.staff(hid, "doctor", department_id=dept)
    admin = auth_headers(client, factory.staff(hid, "hospital_admin")["email"])
    rec = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    url = f"/api/v1/hospitals/{hid}/doctors/{doc['doctor_id']}/schedule-templates"
    body = {"weekday": 0, "start_time": "09:00", "end_time": "12:00", "slot_minutes": 20, "valid_from": "2026-01-01"}

    assert client.post(url, json=body, headers=rec).status_code == 403
    created = client.post(url, json=body, headers=admin)
    assert created.status_code == 201, created.get_json()
    overlap = client.post(url, json={**body, "start_time": "11:00", "end_time": "13:00"}, headers=admin)
    assert overlap.status_code == 409 and overlap.get_json()["error"]["code"] == "schedule_overlap"
    assert client.post(url, json={**body, "start_time": "12:00", "end_time": "14:00"}, headers=admin).status_code == 201
    assert client.post(url, json={**body, "end_time": "08:00"}, headers=admin).status_code == 422

    assert len(client.get(url, headers=rec).get_json()) == 2  # any staff can view
    tid = created.get_json()["id"]
    assert client.delete(f"/api/v1/hospitals/{hid}/schedule-templates/{tid}", headers=admin).status_code == 204
    assert len(client.get(url, headers=rec).get_json()) == 1


def test_patient_cannot_view_templates(client, factory):
    c = factory.clinic()
    p = register_patient(client)
    url = f"/api/v1/hospitals/{c['hospital_id']}/doctors/{c['doctor']['doctor_id']}/schedule-templates"
    assert client.get(url, headers=auth_headers(client, p["email"])).status_code == 403


def test_cannot_schedule_doctor_of_other_hospital(client, factory):
    a = factory.clinic("HOSA")
    b = factory.clinic("HOSB")
    admin_a = auth_headers(client, a["admin"]["email"])
    url = f"/api/v1/hospitals/{a['hospital_id']}/doctors/{b['doctor']['doctor_id']}/schedule-templates"
    body = {"weekday": 1, "start_time": "09:00", "end_time": "12:00", "slot_minutes": 15, "valid_from": "2026-01-01"}
    assert client.post(url, json=body, headers=admin_a).status_code == 404


def test_slots_generated_in_hospital_timezone(client, factory):
    c = factory.clinic()
    data = _slots(client, c["doctor"]["doctor_id"], _day())
    assert data["timezone"] == "Africa/Lagos"
    slots = data["slots"]
    assert len(slots) == 12  # 09:00-12:00 in 15-minute slots
    assert all(s["available"] for s in slots)
    first = datetime.fromisoformat(slots[0]["start_at"])
    assert first.astimezone(LAGOS).hour == 9
    assert first.utcoffset().total_seconds() == 0  # API returns UTC


def test_past_and_far_future_slots_are_unavailable(client, factory):
    c = factory.clinic()
    assert not any(s["available"] for s in _slots(client, c["doctor"]["doctor_id"], _day(-1))["slots"])
    assert not any(s["available"] for s in _slots(client, c["doctor"]["doctor_id"], _day(90))["slots"])


def test_exceptions_block_and_add_time(client, factory):
    c = factory.clinic()
    hid, did = c["hospital_id"], c["doctor"]["doctor_id"]
    admin = auth_headers(client, c["admin"]["email"])
    url = f"/api/v1/hospitals/{hid}/doctors/{did}/schedule-exceptions"

    resp = client.post(url, json={"on_date": _day(7), "kind": "unavailable", "start_time": "10:00",
                                  "end_time": "11:00", "reason": "Department meeting"}, headers=admin)
    assert resp.status_code == 201
    assert len(_slots(client, did, _day(7))["slots"]) == 8

    client.post(url, json={"on_date": _day(8), "kind": "unavailable", "reason": "Leave"}, headers=admin)
    assert _slots(client, did, _day(8))["slots"] == []

    client.post(url, json={"on_date": _day(9), "kind": "extra_session", "start_time": "14:00", "end_time": "15:00",
                           "slot_minutes": 30}, headers=admin)
    assert len(_slots(client, did, _day(9))["slots"]) == 14

    bad = client.post(url, json={"on_date": _day(9), "kind": "extra_session"}, headers=admin)
    assert bad.status_code == 422

    exc_id = resp.get_json()["id"]
    assert client.delete(f"/api/v1/hospitals/{hid}/schedule-exceptions/{exc_id}", headers=admin).status_code == 204
    assert len(_slots(client, did, _day(7))["slots"]) == 12


def test_approved_appointment_makes_slot_unavailable(client, factory):
    c = factory.clinic()
    p = register_patient(client)
    appt = client.post("/api/v1/appointments", headers=auth_headers(client, p["email"]),
                       json={"doctor_id": str(c["doctor"]["doctor_id"]), "start_at": slot_time(7, 9, 0)}).get_json()
    # A pending request does not block the slot...
    assert _slots(client, c["doctor"]["doctor_id"], _day())["slots"][0]["available"] is True
    client.post(f"/api/v1/appointments/{appt['id']}/approve", json={},
                headers=auth_headers(client, c["receptionist"]["email"]))
    # ...an approved appointment does, without revealing who holds it.
    slot = _slots(client, c["doctor"]["doctor_id"], _day())["slots"][0]
    assert slot["available"] is False
    assert set(slot) == {"start_at", "end_at", "available"}
