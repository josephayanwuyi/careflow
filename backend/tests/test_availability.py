from datetime import UTC, datetime, time, timedelta

import pytest

from careflow.extensions import db
from careflow.models import DoctorProfile
from tests.conftest import auth_headers, register_patient


@pytest.fixture
def clinic(factory):
    hid = factory.hospital("HOSA")
    dept = factory.department(hid)
    doctor = factory.staff(hid, "doctor", department_id=dept)
    factory.schedule(hid, doctor["doctor_id"], start=time(0, 0), end=time(23, 45))
    return {"hospital_id": hid, "doctor": doctor, "receptionist": factory.staff(hid, "receptionist")}


def set_status(client, headers, status, **extra):
    return client.post("/api/v1/doctors/me/status", json={"status": status, **extra}, headers=headers)


def public(client, clinic):
    return client.get(f"/api/v1/doctors/{clinic['doctor']['doctor_id']}/availability").get_json()["public_status"]


def test_doctor_day(client, clinic):
    doc = auth_headers(client, clinic["doctor"]["email"])
    assert public(client, clinic) == "expected"

    bad = set_status(client, doc, "available")
    assert bad.status_code == 409 and bad.get_json()["error"]["code"] == "invalid_status_change"

    arrived = set_status(client, doc, "checked_in")
    assert arrived.status_code == 200
    assert arrived.get_json()["status_source"] == "self_reported"
    assert public(client, clinic) == "checked_in"

    assert set_status(client, doc, "available").get_json()["status"] == "available"
    assert public(client, clinic) == "available"
    assert set_status(client, doc, "available").status_code == 409  # unchanged

    back_at = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    brk = set_status(client, doc, "on_break", expected_return_at=back_at, note="Lunch")
    assert brk.status_code == 200 and brk.get_json()["expected_return_at"]
    assert public(client, clinic) == "on_break"

    assert set_status(client, doc, "checked_out").status_code == 200
    assert public(client, clinic) == "unavailable"

    history = client.get(f"/api/v1/doctors/{clinic['doctor']['doctor_id']}/status-history", headers=doc).get_json()
    assert [h["status"] for h in history] == ["checked_out", "on_break", "available", "checked_in"]


def test_expected_return_validation(client, clinic):
    doc = auth_headers(client, clinic["doctor"]["email"])
    set_status(client, doc, "checked_in")
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    assert set_status(client, doc, "on_break", expected_return_at=past).status_code == 422
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    assert set_status(client, doc, "available", expected_return_at=future).status_code == 422


def test_status_resets_each_day(client, app, clinic):
    doc = auth_headers(client, clinic["doctor"]["email"])
    set_status(client, doc, "checked_in")
    set_status(client, doc, "available")
    with app.app_context():
        profile = db.session.get(DoctorProfile, clinic["doctor"]["doctor_id"])
        profile.current_status_at -= timedelta(days=1)
        db.session.commit()
    assert public(client, clinic) == "expected"
    # Must check in again today before becoming available.
    assert set_status(client, doc, "available").status_code == 409


def test_receptionist_verifies_arrival(client, clinic):
    rec = auth_headers(client, clinic["receptionist"]["email"])
    hid, did = clinic["hospital_id"], clinic["doctor"]["doctor_id"]
    resp = client.post(f"/api/v1/hospitals/{hid}/doctors/{did}/verify-arrival", json={"note": "Seen at desk"}, headers=rec)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "checked_in" and data["status_source"] == "staff_verified"
    assert data["arrival_verified_today"] is True

    listing = client.get(f"/api/v1/hospitals/{hid}/doctors/availability", headers=rec).get_json()
    assert listing[0]["arrival_verified_today"] is True


def test_only_doctors_set_own_status(client, clinic):
    rec = auth_headers(client, clinic["receptionist"]["email"])
    assert set_status(client, rec, "checked_in").status_code == 403
    p = register_patient(client)
    assert set_status(client, auth_headers(client, p["email"]), "checked_in").status_code == 403


def test_doctor_cannot_verify_or_see_other_history(client, factory, clinic):
    other = factory.staff(clinic["hospital_id"], "doctor")
    other_h = auth_headers(client, other["email"])
    hid, did = clinic["hospital_id"], clinic["doctor"]["doctor_id"]
    assert client.post(f"/api/v1/hospitals/{hid}/doctors/{did}/verify-arrival", json={}, headers=other_h).status_code == 403
    assert client.get(f"/api/v1/doctors/{did}/status-history", headers=other_h).status_code == 404
    rec = auth_headers(client, clinic["receptionist"]["email"])
    assert client.get(f"/api/v1/doctors/{did}/status-history", headers=rec).status_code == 200


def test_cannot_check_out_during_consultation(client, clinic):
    rec = auth_headers(client, clinic["receptionist"]["email"])
    doc = auth_headers(client, clinic["doctor"]["email"])
    hid = clinic["hospital_id"]
    patient = client.post(f"/api/v1/hospitals/{hid}/patients", json={"full_name": "Some Patient"}, headers=rec).get_json()
    entry = client.post(f"/api/v1/hospitals/{hid}/queue/walk-ins", headers=rec,
                        json={"patient_id": patient["id"], "doctor_id": str(clinic["doctor"]["doctor_id"])}).get_json()
    set_status(client, doc, "checked_in")
    set_status(client, doc, "available")
    client.post(f"/api/v1/queues/{entry['queue_id']}/call-next", headers=doc)
    client.post(f"/api/v1/queue-entries/{entry['id']}/start", json={}, headers=doc)
    resp = set_status(client, doc, "checked_out")
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "consultation_in_progress"


def test_public_availability_exposes_only_a_label(client, clinic):
    data = client.get(f"/api/v1/doctors/{clinic['doctor']['doctor_id']}/availability").get_json()
    assert set(data) == {"doctor_id", "public_status"}
