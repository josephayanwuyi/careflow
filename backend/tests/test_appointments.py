"""Appointment lifecycle through the API."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from careflow.extensions import db
from careflow.models import Appointment, DoctorProfile
from tests.conftest import auth_headers, register_patient, slot_time


@pytest.fixture
def clinic(factory):
    return factory.clinic()


@pytest.fixture
def patient(client):
    p = register_patient(client)
    p["headers"] = auth_headers(client, p["email"])
    return p


@pytest.fixture
def rec(client, clinic):
    return auth_headers(client, clinic["receptionist"]["email"])


def request_appt(client, clinic, patient, start=None, **extra):
    body = {"doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": start or slot_time(), **extra}
    headers = {**patient["headers"], **extra.pop("headers", {})} if "headers" in extra else patient["headers"]
    return client.post("/api/v1/appointments", json=body, headers=headers)


def act(client, appt_id, action, headers, **body):
    return client.post(f"/api/v1/appointments/{appt_id}/{action}", json=body, headers=headers)


def move_appointment(app, appt_id, delta: timedelta):
    """Shift an appointment in time (to simulate the clock moving forward)."""
    with app.app_context():
        appt = db.session.get(Appointment, uuid.UUID(appt_id))
        appt.start_at += delta
        appt.end_at += delta
        if appt.proposed_start_at:
            appt.proposed_start_at += delta
            appt.proposed_end_at += delta
        db.session.commit()


# --- Requests ---------------------------------------------------------------------------


def test_request_is_pending_not_approved(client, clinic, patient):
    resp = request_appt(client, clinic, patient, reason_for_visit="Follow-up")
    assert resp.status_code == 201, resp.get_json()
    appt = resp.get_json()
    assert appt["status"] == "requested"
    assert appt["allowed_actions"] == ["cancel"]
    assert appt["patient"] is None  # patients don't get the staff summary block
    assert appt["doctor_name"] and appt["department_name"] and appt["hospital_timezone"] == "Africa/Lagos"

    notes = client.get("/api/v1/notifications", headers=patient["headers"]).get_json()
    assert notes["unread_count"] == 1
    assert "not confirmed" in notes["items"][0]["body"]


def test_request_creates_hospital_record(client, clinic, patient, rec):
    request_appt(client, clinic, patient)
    found = client.get(f"/api/v1/hospitals/{clinic['hospital_id']}/patients",
                       query_string={"q": "Synthetic Patient"}, headers=rec).get_json()
    assert [p["id"] for p in found] == [patient["patient_id"]]


@pytest.mark.parametrize("start, code", [
    (lambda: slot_time(7, 9, 5), "not_a_slot"),
    (lambda: slot_time(7, 13, 0), "not_a_slot"),
    (lambda: slot_time(-1, 9, 0), "slot_too_soon"),
    (lambda: slot_time(120, 9, 0), "slot_too_far_ahead"),
])
def test_request_must_match_a_bookable_slot(client, clinic, patient, start, code):
    resp = request_appt(client, clinic, patient, start=start())
    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == code


def test_request_requires_timezone(client, clinic, patient):
    resp = request_appt(client, clinic, patient, start=slot_time()[:19])  # strip offset
    assert resp.status_code == 422


def test_request_validates_department(client, factory, clinic, patient):
    other = factory.department(clinic["hospital_id"], "Other Dept")
    resp = request_appt(client, clinic, patient, department_id=str(other))
    assert resp.status_code == 422


def test_doctor_not_accepting(client, app, clinic, patient):
    with app.app_context():
        db.session.get(DoctorProfile, clinic["doctor"]["doctor_id"]).is_accepting_appointments = False
        db.session.commit()
    resp = request_appt(client, clinic, patient)
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "doctor_not_accepting"


def test_staff_cannot_submit_patient_requests(client, clinic, rec):
    resp = client.post("/api/v1/appointments", headers=rec,
                       json={"doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()})
    assert resp.status_code == 403


def test_idempotent_retry_returns_original(client, clinic, patient):
    headers = {**patient["headers"], "Idempotency-Key": "retry-key-0001"}
    body = {"doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()}
    first = client.post("/api/v1/appointments", json=body, headers=headers)
    second = client.post("/api/v1/appointments", json=body, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.get_json()["id"] == second.get_json()["id"]
    different = client.post("/api/v1/appointments", json={**body, "start_at": slot_time(7, 9, 15)}, headers=headers)
    assert different.status_code == 409 and different.get_json()["error"]["code"] == "idempotency_key_reused"
    bad_key = client.post("/api/v1/appointments", json=body, headers={**patient["headers"], "Idempotency-Key": "x"})
    assert bad_key.status_code == 422


def test_duplicate_request_without_key_rejected(client, clinic, patient):
    assert request_appt(client, clinic, patient).status_code == 201
    dup = request_appt(client, clinic, patient)
    assert dup.status_code == 409 and dup.get_json()["error"]["code"] == "duplicate_request"


def test_open_request_limit(client, clinic, patient):
    for minute in (0, 15, 30, 45):
        assert request_appt(client, clinic, patient, start=slot_time(7, 9, minute)).status_code == 201
    assert request_appt(client, clinic, patient, start=slot_time(7, 10, 0)).status_code == 201
    over = request_appt(client, clinic, patient, start=slot_time(7, 10, 15))
    assert over.status_code == 409 and over.get_json()["error"]["code"] == "too_many_open_requests"


# --- Review -----------------------------------------------------------------------------


def test_receptionist_approves_and_patient_sees_it(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    pending = client.get(f"/api/v1/hospitals/{clinic['hospital_id']}/appointments?status=requested", headers=rec).get_json()
    assert [a["id"] for a in pending["items"]] == [appt["id"]]
    staff_view = pending["items"][0]
    assert staff_view["patient"]["full_name"] == "Synthetic Patient"
    assert staff_view["patient"]["hospital_patient_number"].startswith("HOSA-")
    assert set(staff_view["allowed_actions"]) == {"approve", "reject", "propose_reschedule", "cancel"}

    approved = act(client, appt["id"], "approve", rec, expected_version=appt["version"])
    assert approved.status_code == 200, approved.get_json()
    assert approved.get_json()["status"] == "approved"

    mine = client.get("/api/v1/appointments/mine", headers=patient["headers"]).get_json()["items"]
    assert mine[0]["status"] == "approved"
    titles = [n["title"] for n in client.get("/api/v1/notifications", headers=patient["headers"]).get_json()["items"]]
    assert titles[0] == "Appointment approved"


def test_two_requests_same_slot_only_one_can_be_approved(client, clinic, rec):
    p1, p2 = register_patient(client), register_patient(client)
    a1 = request_appt(client, clinic, {"headers": auth_headers(client, p1["email"])}).get_json()
    a2 = request_appt(client, clinic, {"headers": auth_headers(client, p2["email"])}).get_json()
    assert act(client, a1["id"], "approve", rec).status_code == 200
    second = act(client, a2["id"], "approve", rec)
    assert second.status_code == 409 and second.get_json()["error"]["code"] == "slot_unavailable"


def test_patient_cannot_double_book_themselves(client, factory, clinic, patient, rec):
    other_doc = factory.staff(clinic["hospital_id"], "doctor", department_id=clinic["department_id"])
    factory.schedule(clinic["hospital_id"], other_doc["doctor_id"])
    a1 = request_appt(client, clinic, patient).get_json()
    a2 = client.post("/api/v1/appointments", headers=patient["headers"],
                     json={"doctor_id": str(other_doc["doctor_id"]), "start_at": slot_time()}).get_json()
    assert act(client, a1["id"], "approve", rec).status_code == 200
    resp = act(client, a2["id"], "approve", rec)
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "patient_time_conflict"


def test_reject_requires_reason_and_notifies(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    assert act(client, appt["id"], "reject", rec).status_code == 422
    resp = act(client, appt["id"], "reject", rec, reason="Doctor fully booked")
    assert resp.get_json()["status"] == "rejected"
    assert resp.get_json()["status_reason"] == "Doctor fully booked"
    body = client.get("/api/v1/notifications", headers=patient["headers"]).get_json()["items"][0]["body"]
    assert "Doctor fully booked" in body


def test_invalid_transition_and_stale_version(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "reject", rec, reason="Not available")
    again = act(client, appt["id"], "approve", rec)
    assert again.status_code == 409 and again.get_json()["error"]["code"] == "invalid_transition"

    appt2 = request_appt(client, clinic, patient, start=slot_time(7, 9, 15)).get_json()
    stale = act(client, appt2["id"], "approve", rec, expected_version=appt2["version"] + 5)
    assert stale.status_code == 409 and stale.get_json()["error"]["code"] == "stale_version"


def test_patient_and_doctor_cannot_review(client, clinic, patient):
    appt = request_appt(client, clinic, patient).get_json()
    assert act(client, appt["id"], "approve", patient["headers"]).status_code == 403
    doctor = auth_headers(client, clinic["doctor"]["email"])
    assert act(client, appt["id"], "approve", doctor).status_code == 403


def test_visibility_boundaries(client, factory, clinic, patient):
    appt = request_appt(client, clinic, patient).get_json()
    url = f"/api/v1/appointments/{appt['id']}"

    other_patient = register_patient(client)
    assert client.get(url, headers=auth_headers(client, other_patient["email"])).status_code == 404

    other_clinic = factory.clinic("HOSB")
    assert client.get(url, headers=auth_headers(client, other_clinic["receptionist"]["email"])).status_code == 404
    assert act(client, appt["id"], "approve", auth_headers(client, other_clinic["receptionist"]["email"])).status_code == 404

    other_doc = factory.staff(clinic["hospital_id"], "doctor", department_id=clinic["department_id"])
    assert client.get(url, headers=auth_headers(client, other_doc["email"])).status_code == 404

    own_doc = client.get(url, headers=auth_headers(client, clinic["doctor"]["email"]))
    assert own_doc.status_code == 200 and own_doc.get_json()["patient"]["full_name"] == "Synthetic Patient"
    assert own_doc.get_json()["allowed_actions"] == []


def test_doctor_list_is_limited_to_own(client, factory, clinic, patient):
    request_appt(client, clinic, patient)
    other_doc = factory.staff(clinic["hospital_id"], "doctor", department_id=clinic["department_id"])
    hid = clinic["hospital_id"]
    other = auth_headers(client, other_doc["email"])
    assert client.get(f"/api/v1/hospitals/{hid}/appointments", headers=other).get_json()["items"] == []
    own = client.get(f"/api/v1/hospitals/{hid}/appointments", headers=auth_headers(client, clinic["doctor"]["email"]))
    assert len(own.get_json()["items"]) == 1
    spoof = client.get(f"/api/v1/hospitals/{hid}/appointments",
                       query_string={"doctor_id": str(clinic["doctor"]["doctor_id"])}, headers=other)
    assert spoof.status_code == 403


def test_hospital_list_filters_and_pagination(client, clinic, patient, rec):
    for minute in (0, 15, 30):
        request_appt(client, clinic, patient, start=slot_time(7, 9, minute))
    url = f"/api/v1/hospitals/{clinic['hospital_id']}/appointments"
    page1 = client.get(url, query_string={"limit": 2}, headers=rec).get_json()
    page2 = client.get(url, query_string={"limit": 2, "cursor": page1["next_cursor"]}, headers=rec).get_json()
    starts = [a["start_at"] for a in page1["items"] + page2["items"]]
    assert len(starts) == 3 and starts == sorted(starts) and page2["next_cursor"] is None

    day = slot_time()[:10]
    assert len(client.get(url, query_string={"date_from": day, "date_to": day}, headers=rec).get_json()["items"]) == 3
    assert client.get(url, query_string={"status": "approved,rejected"}, headers=rec).get_json()["items"] == []
    assert client.get(url, query_string={"status": "bogus"}, headers=rec).status_code == 422


# --- Rescheduling and cancellation -------------------------------------------------------


def test_reschedule_proposal_accepted(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "approve", rec)
    new_time = slot_time(8, 10, 0)
    proposed = act(client, appt["id"], "propose-reschedule", rec, start_at=new_time, reason="Doctor in theatre")
    assert proposed.status_code == 200, proposed.get_json()
    assert proposed.get_json()["status"] == "reschedule_proposed"

    mine = client.get(f"/api/v1/appointments/{appt['id']}", headers=patient["headers"]).get_json()
    assert set(mine["allowed_actions"]) == {"accept_reschedule", "decline_reschedule", "cancel"}
    accepted = act(client, appt["id"], "accept-reschedule", patient["headers"])
    assert accepted.status_code == 200
    data = accepted.get_json()
    assert data["status"] == "approved" and data["proposed_start_at"] is None
    assert data["start_at"][:16] == _utc_iso(new_time)[:16]

    history = client.get(f"/api/v1/appointments/{appt['id']}/history", headers=patient["headers"]).get_json()
    assert [h["event"] for h in history] == ["requested", "approved", "reschedule_proposed", "rescheduled"]
    assert all(h["actor_user_id"] is None for h in history)  # patients don't see staff identities
    staff_history = client.get(f"/api/v1/appointments/{appt['id']}/history", headers=rec).get_json()
    assert staff_history[1]["actor_user_id"] == str(clinic["receptionist"]["user_id"])


def _utc_iso(local_iso):
    from datetime import UTC, datetime

    return datetime.fromisoformat(local_iso).astimezone(UTC).isoformat()


def test_proposal_holds_the_new_time_and_releases_the_old(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "approve", rec)
    act(client, appt["id"], "propose-reschedule", rec, start_at=slot_time(7, 10, 0), reason="Clinic change")
    other = register_patient(client)
    other_h = {"headers": auth_headers(client, other["email"])}
    # The original 09:00 slot is free again; the proposed 10:00 slot is held.
    assert request_appt(client, clinic, other_h, start=slot_time(7, 9, 0)).status_code == 201
    held = request_appt(client, clinic, other_h, start=slot_time(7, 10, 0))
    assert held.status_code == 409 and held.get_json()["error"]["code"] == "slot_unavailable"


def test_proposal_must_be_a_new_valid_slot(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    assert act(client, appt["id"], "propose-reschedule", rec, start_at=slot_time(), reason="x" * 5).status_code == 422
    assert act(client, appt["id"], "propose-reschedule", rec, start_at=slot_time(7, 9, 7), reason="Moving it").status_code == 409


def test_decline_reschedule_cancels(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "propose-reschedule", rec, start_at=slot_time(7, 10, 0), reason="Doctor away")
    resp = act(client, appt["id"], "decline-reschedule", patient["headers"], reason="Can't make it")
    assert resp.get_json()["status"] == "cancelled"


def test_patient_cancels_and_slot_is_released(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "approve", rec)
    resp = act(client, appt["id"], "cancel", patient["headers"])
    assert resp.status_code == 200 and resp.get_json()["status"] == "cancelled"
    other = register_patient(client)
    a2 = request_appt(client, clinic, {"headers": auth_headers(client, other["email"])}).get_json()
    assert act(client, a2["id"], "approve", rec).status_code == 200


def test_staff_cancel_requires_reason(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "approve", rec)
    assert act(client, appt["id"], "cancel", rec).status_code == 422
    resp = act(client, appt["id"], "cancel", rec, reason="Doctor on emergency leave")
    assert resp.get_json()["status"] == "cancelled"


def test_cannot_cancel_completed_states(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "reject", rec, reason="No capacity")
    assert act(client, appt["id"], "cancel", patient["headers"]).status_code == 409


# --- Time-based ----------------------------------------------------------------------------


def test_unreviewed_request_expires(client, app, clinic, patient):
    appt = request_appt(client, clinic, patient).get_json()
    move_appointment(app, appt["id"], timedelta(days=-8))
    mine = client.get("/api/v1/appointments/mine", headers=patient["headers"]).get_json()["items"]
    assert mine[0]["status"] == "expired"
    assert client.get("/api/v1/notifications", headers=patient["headers"]).get_json()["items"][0]["title"] == \
        "Appointment request expired"


def test_cannot_approve_past_request(client, app, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    move_appointment(app, appt["id"], timedelta(days=-8))
    # Listing triggers expiry, so approval is no longer possible.
    resp = act(client, appt["id"], "approve", rec)
    assert resp.status_code == 409


def test_mark_no_show_after_grace_period(client, app, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "approve", rec)
    early = act(client, appt["id"], "mark-no-show", rec)
    assert early.status_code == 409 and early.get_json()["error"]["code"] == "too_early_for_no_show"
    move_appointment(app, appt["id"], timedelta(days=-7, hours=-2))
    resp = act(client, appt["id"], "mark-no-show", rec, reason="Did not arrive")
    assert resp.status_code == 200 and resp.get_json()["status"] == "no_show"


# --- Staff booking ------------------------------------------------------------------------


def test_staff_books_for_registered_patient(client, clinic, rec):
    hid = clinic["hospital_id"]
    walk_in = client.post(f"/api/v1/hospitals/{hid}/patients", json={"full_name": "Phone Caller"}, headers=rec).get_json()
    resp = client.post(f"/api/v1/hospitals/{hid}/appointments", headers=rec, json={
        "patient_id": walk_in["id"], "doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()})
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["status"] == "approved" and resp.get_json()["source"] == "staff_booked"


def test_staff_cannot_book_unknown_patient_or_other_hospital(client, factory, clinic, patient, rec):
    hid = clinic["hospital_id"]
    body = {"patient_id": patient["patient_id"], "doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()}
    # The patient has no record at this hospital yet.
    assert client.post(f"/api/v1/hospitals/{hid}/appointments", json=body, headers=rec).status_code == 404
    other = factory.clinic("HOSB")
    other_rec = auth_headers(client, other["receptionist"]["email"])
    assert client.post(f"/api/v1/hospitals/{hid}/appointments", json=body, headers=other_rec).status_code == 403


# --- Audit and history integrity ----------------------------------------------------------


def test_actions_are_audited(client, clinic, patient, rec):
    appt = request_appt(client, clinic, patient).get_json()
    act(client, appt["id"], "approve", rec)
    events = client.get(f"/api/v1/hospitals/{clinic['hospital_id']}/audit-events",
                        query_string={"target_type": "appointment", "target_id": appt["id"]}, headers=rec).get_json()
    assert {e["action"] for e in events["items"]} == {"appointment.requested", "appointment.approved"}


def test_status_history_is_append_only(client, app, clinic, patient):
    appt = request_appt(client, clinic, patient).get_json()
    with app.app_context():
        with pytest.raises(DBAPIError, match="append-only"):
            db.session.execute(text("DELETE FROM appointment_status_history WHERE appointment_id = :id"),
                               {"id": appt["id"]})
        db.session.rollback()


def test_database_rejects_double_booking_directly(client, app, clinic, rec):
    """The exclusion constraint holds even if application checks were bypassed."""
    p1, p2 = register_patient(client), register_patient(client)
    a1 = request_appt(client, clinic, {"headers": auth_headers(client, p1["email"])}).get_json()
    a2 = request_appt(client, clinic, {"headers": auth_headers(client, p2["email"])}).get_json()
    act(client, a1["id"], "approve", rec)
    with app.app_context():
        with pytest.raises(DBAPIError):
            db.session.execute(text("UPDATE appointments SET status = 'approved' WHERE id = :id"), {"id": a2["id"]})
            db.session.flush()
        db.session.rollback()
