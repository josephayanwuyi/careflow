"""Queue engine through the API."""

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from careflow.extensions import db
from careflow.models import Appointment, ConsultationSession, Queue
from tests.conftest import auth_headers, register_patient


def now_utc():
    return datetime.now(UTC).replace(second=0, microsecond=0)


@pytest.fixture
def clinic(factory):
    """A clinic whose doctor has a session covering the whole day, so tests work at any hour."""
    hid = factory.hospital("HOSA")
    dept = factory.department(hid)
    doctor = factory.staff(hid, "doctor", department_id=dept)
    factory.schedule(hid, doctor["doctor_id"], start=time(0, 0), end=time(23, 45))
    return {"hospital_id": hid, "department_id": dept, "doctor": doctor,
            "receptionist": factory.staff(hid, "receptionist"), "admin": factory.staff(hid, "hospital_admin")}


@pytest.fixture
def H(client, clinic):
    """Auth headers by role."""
    return {
        "rec": auth_headers(client, clinic["receptionist"]["email"]),
        "admin": auth_headers(client, clinic["admin"]["email"]),
        "doc": auth_headers(client, clinic["doctor"]["email"]),
    }


def make_patient(client):
    p = register_patient(client)
    p["headers"] = auth_headers(client, p["email"])
    return p


def booked(factory, clinic, patient, minutes_from_now=0):
    """An approved appointment starting around now (each patient at a distinct minute)."""
    return factory.approved_appointment(clinic["hospital_id"], clinic["doctor"]["doctor_id"], patient["patient_id"],
                                        start_at=now_utc() + timedelta(minutes=minutes_from_now))


def check_in(client, appt_id, headers):
    return client.post(f"/api/v1/appointments/{appt_id}/check-in", headers=headers)


def doctor_available(client, H):
    for status in ("checked_in", "available"):
        resp = client.post("/api/v1/doctors/me/status", json={"status": status}, headers=H["doc"])
        assert resp.status_code == 200, resp.get_json()


def entries(client, queue_id, headers, **params):
    resp = client.get(f"/api/v1/queues/{queue_id}/entries", query_string=params, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def act(client, entry_id, action, headers, **body):
    return client.post(f"/api/v1/queue-entries/{entry_id}/{action}", json=body, headers=headers)


def walk_in(client, clinic, H, name="Walk In Person", doctor_id=None):
    hid = clinic["hospital_id"]
    patient = client.post(f"/api/v1/hospitals/{hid}/patients", json={"full_name": name}, headers=H["rec"]).get_json()
    resp = client.post(f"/api/v1/hospitals/{hid}/queue/walk-ins", headers=H["rec"],
                       json={"patient_id": patient["id"], "doctor_id": str(doctor_id or clinic["doctor"]["doctor_id"])})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


# --- Check-in -------------------------------------------------------------------------------


def test_patient_self_check_in(client, factory, clinic):
    p = make_patient(client)
    appt_id = booked(factory, clinic, p, 10)
    resp = check_in(client, appt_id, p["headers"])
    assert resp.status_code == 201, resp.get_json()
    entry = resp.get_json()
    assert entry["status"] == "waiting" and entry["ticket_number"] == 1 and entry["position"] == 1
    assert entry["patient"] is None  # patients don't get the staff block
    assert entry["estimate"]["state"] in ("unavailable", "next", "estimated")
    assert entry["allowed_actions"] == ["cancel"]
    appt = client.get(f"/api/v1/appointments/{appt_id}", headers=p["headers"]).get_json()
    assert appt["status"] == "checked_in"
    assert check_in(client, appt_id, p["headers"]).status_code == 409


def test_check_in_window(client, factory, clinic):
    p = make_patient(client)
    early = booked(factory, clinic, p, 180)
    resp = check_in(client, early, p["headers"])
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "checkin_not_open"
    p2 = make_patient(client)
    late = booked(factory, clinic, p2, -90)
    resp = check_in(client, late, p2["headers"])
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "checkin_closed"


def test_self_check_in_can_be_disabled(client, factory, clinic, H):
    factory.set_settings(clinic["hospital_id"], allow_patient_self_checkin=0)
    p = make_patient(client)
    appt_id = booked(factory, clinic, p)
    assert check_in(client, appt_id, p["headers"]).status_code == 403
    staff = check_in(client, appt_id, H["rec"])
    assert staff.status_code == 201 and staff.get_json()["check_in_method"] == "staff"
    assert staff.get_json()["arrival_confirmed_at"]


def test_cannot_check_in_someone_elses_appointment(client, factory, clinic):
    p1, p2 = make_patient(client), make_patient(client)
    appt_id = booked(factory, clinic, p1)
    assert check_in(client, appt_id, p2["headers"]).status_code == 404


def test_unapproved_appointment_cannot_check_in(client, app, factory, clinic):
    p = make_patient(client)
    appt_id = booked(factory, clinic, p)
    with app.app_context():
        db.session.get(Appointment, uuid.UUID(appt_id)).status = "requested"
        db.session.commit()
    resp = check_in(client, appt_id, p["headers"])
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "invalid_transition"


def test_walk_in_permissions(client, clinic, H):
    entry = walk_in(client, clinic, H)
    assert entry["kind"] == "walk_in" and entry["status"] == "waiting"
    hid = clinic["hospital_id"]
    body = {"patient_id": entry["patient"]["id"], "doctor_id": str(clinic["doctor"]["doctor_id"])}
    assert client.post(f"/api/v1/hospitals/{hid}/queue/walk-ins", json=body, headers=H["doc"]).status_code == 403
    dup = client.post(f"/api/v1/hospitals/{hid}/queue/walk-ins", json=body, headers=H["rec"])
    assert dup.status_code == 409 and dup.get_json()["error"]["code"] == "already_in_queue"
    unknown = {**body, "patient_id": str(uuid.uuid4())}
    assert client.post(f"/api/v1/hospitals/{hid}/queue/walk-ins", json=unknown, headers=H["rec"]).status_code == 404


# --- Ordering ----------------------------------------------------------------------------------


def test_queue_order_follows_documented_rules(client, factory, clinic, H):
    late = make_patient(client)
    punctual = make_patient(client)
    late_appt = booked(factory, clinic, late, -40)        # checks in 40 min after start: loses booked place
    punctual_appt = booked(factory, clinic, punctual, 5)  # checks in before start: keeps booked place
    q = check_in(client, late_appt, H["rec"]).get_json()["queue_id"]
    walk = walk_in(client, clinic, H)                      # walk-in: check-in time (now)
    check_in(client, punctual_appt, H["rec"])

    names = [e["patient"]["id"] for e in entries(client, q, H["rec"])]
    # late patient's sort key = check-in time (earliest of the three check-ins); walk-in next; punctual = start+5min
    assert names == [late["patient_id"], walk["patient"]["id"], punctual["patient_id"]]
    assert [e["position"] for e in entries(client, q, H["rec"])] == [1, 2, 3]


def test_priority_requires_permission_and_reorders(client, factory, clinic, H):
    first, second = walk_in(client, clinic, H, "First Person"), walk_in(client, clinic, H, "Second Person")
    denied = act(client, second["id"], "priority", H["rec"], level=2, reason="Clinical review by nurse")
    assert denied.status_code == 403
    assert act(client, second["id"], "priority", H["admin"], level=2, reason="x").status_code == 422
    ok = act(client, second["id"], "priority", H["admin"], level=2, reason="Clinical review by nurse")
    assert ok.status_code == 200 and ok.get_json()["priority_level"] == 2
    ordered = [e["id"] for e in entries(client, first["queue_id"], H["rec"])]
    assert ordered == [second["id"], first["id"]]


# --- Calling and consultations --------------------------------------------------------------------


def test_full_consultation_cycle(client, app, factory, clinic, H):
    p = make_patient(client)
    appt_id = booked(factory, clinic, p)
    entry = check_in(client, appt_id, p["headers"]).get_json()
    q = entry["queue_id"]

    not_ready = client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])
    assert not_ready.status_code == 409 and not_ready.get_json()["error"]["code"] == "doctor_not_available"
    doctor_available(client, H)

    called = client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])
    assert called.status_code == 200 and called.get_json()["status"] == "called"
    assert called.get_json()["id"] == entry["id"]
    again = client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])
    assert again.status_code == 409 and again.get_json()["error"]["code"] == "patient_already_called"

    mine = client.get("/api/v1/queue/me", headers=p["headers"]).get_json()
    assert mine[0]["status"] == "called" and mine[0]["estimate"]["state"] == "called"
    notes = client.get("/api/v1/notifications", headers=p["headers"]).get_json()["items"]
    assert notes[0]["title"] == "It's your turn"

    started = act(client, entry["id"], "start", H["doc"])
    assert started.get_json()["status"] == "in_consultation"
    assert client.get(f"/api/v1/doctors/{clinic['doctor']['doctor_id']}/availability").get_json()["public_status"] == \
        "consultation_in_progress"
    done = act(client, entry["id"], "complete", H["doc"])
    assert done.status_code == 200 and done.get_json()["status"] == "completed"

    assert client.get(f"/api/v1/appointments/{appt_id}", headers=p["headers"]).get_json()["status"] == "completed"
    with app.app_context():
        session = db.session.query(ConsultationSession).one()
        assert session.ended_at is not None and session.duration_seconds >= 0
    events = [e["event_type"] for e in client.get(f"/api/v1/queue-entries/{entry['id']}/events", headers=H["rec"]).get_json()]
    assert events == ["checked_in", "called", "consultation_started", "consultation_completed"]
    history = [h["event"] for h in client.get(f"/api/v1/appointments/{appt_id}/history", headers=H["rec"]).get_json()]
    assert history[-2:] == ["checked_in", "completed"]

    empty = client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])
    assert empty.status_code == 409 and empty.get_json()["error"]["code"] == "queue_empty"


def test_paused_queue_cannot_call(client, clinic, H):
    entry = walk_in(client, clinic, H)
    doctor_available(client, H)
    q = entry["queue_id"]
    assert client.post(f"/api/v1/queues/{q}/pause", json={"reason": "Emergency in ward"}, headers=H["doc"]).status_code == 200
    resp = client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "queue_paused"
    waiting = entries(client, q, H["rec"])[0]
    assert waiting["queue_paused"] is True and waiting["estimate"]["state"] == "unavailable"
    assert client.post(f"/api/v1/queues/{q}/resume", headers=H["rec"]).status_code == 200
    assert client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"]).status_code == 200


def test_missed_call_return_and_limit(client, clinic, H):
    first, second = walk_in(client, clinic, H, "First Person"), walk_in(client, clinic, H, "Second Person")
    q = first["queue_id"]
    doctor_available(client, H)

    client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])
    missed = act(client, first["id"], "mark-missed", H["doc"], reason="No response")
    assert missed.get_json()["status"] == "missed" and missed.get_json()["missed_count"] == 1
    assert act(client, first["id"], "return", H["doc"]).status_code == 403  # doctors can't return patients
    back = act(client, first["id"], "return", H["rec"], reason="Was in the pharmacy")
    assert back.get_json()["status"] == "waiting"
    # Returned patients go to the back of the queue.
    assert [e["id"] for e in entries(client, q, H["rec"])] == [second["id"], first["id"]]

    client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])      # second
    act(client, second["id"], "mark-missed", H["doc"])
    client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])      # first again
    act(client, first["id"], "mark-missed", H["doc"])
    blocked = act(client, first["id"], "return", H["rec"])
    assert blocked.status_code == 409 and blocked.get_json()["error"]["code"] == "too_many_missed_calls"


def test_recall(client, clinic, H):
    entry = walk_in(client, clinic, H)
    doctor_available(client, H)
    client.post(f"/api/v1/queues/{entry['queue_id']}/call-next", headers=H["doc"])
    resp = act(client, entry["id"], "recall", H["doc"])
    assert resp.get_json()["call_count"] == 2


def test_arrival_confirmation_policy(client, factory, clinic, H):
    factory.set_settings(clinic["hospital_id"], require_arrival_confirmation=1)
    p = make_patient(client)
    entry = check_in(client, booked(factory, clinic, p), p["headers"]).get_json()
    doctor_available(client, H)
    q = entry["queue_id"]
    resp = client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"])
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "queue_empty"
    assert act(client, entry["id"], "confirm-arrival", H["rec"]).get_json()["arrival_confirmed_at"]
    assert client.post(f"/api/v1/queues/{q}/call-next", headers=H["doc"]).status_code == 200


# --- Leaving and moving --------------------------------------------------------------------------


def test_patient_cancels_own_place(client, factory, clinic, H):
    p = make_patient(client)
    appt_id = booked(factory, clinic, p)
    entry = check_in(client, appt_id, p["headers"]).get_json()
    resp = act(client, entry["id"], "cancel", p["headers"])
    assert resp.status_code == 200 and resp.get_json()["status"] == "cancelled"
    assert client.get(f"/api/v1/appointments/{appt_id}", headers=p["headers"]).get_json()["status"] == "cancelled"
    assert client.get("/api/v1/queue/me", headers=p["headers"]).get_json() == []


def test_staff_cancel_requires_reason(client, clinic, H):
    entry = walk_in(client, clinic, H)
    assert act(client, entry["id"], "cancel", H["rec"]).status_code == 422
    assert act(client, entry["id"], "cancel", H["rec"], reason="Left the hospital").status_code == 200


def test_transfer_keeps_place_in_line(client, factory, clinic, H):
    other = factory.staff(clinic["hospital_id"], "doctor", department_id=clinic["department_id"])
    early = walk_in(client, clinic, H, "Early Person")
    # Someone already waiting for the other doctor, who checked in after "early".
    later_other = walk_in(client, clinic, H, "Later Person", doctor_id=other["doctor_id"])

    moved = act(client, early["id"], "transfer", H["rec"], doctor_id=str(other["doctor_id"]), reason="Specialist needed")
    assert moved.status_code == 200, moved.get_json()
    new = moved.get_json()
    assert new["id"] != early["id"] and new["queue_id"] == later_other["queue_id"]
    assert [e["id"] for e in entries(client, new["queue_id"], H["rec"])] == [new["id"], later_other["id"]]
    old = client.get(f"/api/v1/queue-entries/{early['id']}", headers=H["rec"]).get_json()
    assert old["status"] == "transferred"
    same = act(client, new["id"], "transfer", H["rec"], doctor_id=str(other["doctor_id"]), reason="Again")
    assert same.status_code == 422


# --- Visibility ----------------------------------------------------------------------------------


def test_queue_visibility(client, factory, clinic, H):
    p1, p2 = make_patient(client), make_patient(client)
    e1 = check_in(client, booked(factory, clinic, p1, 1), p1["headers"]).get_json()
    check_in(client, booked(factory, clinic, p2, 2), p2["headers"])
    q = e1["queue_id"]

    # Patients see only their own place, with no other patients' details.
    mine = client.get("/api/v1/queue/me", headers=p1["headers"]).get_json()
    assert [m["id"] for m in mine] == [e1["id"]] and mine[0]["patient"] is None
    assert client.get(f"/api/v1/queues/{q}/entries", headers=p1["headers"]).status_code == 404
    other_entry = entries(client, q, H["rec"])[1]["id"]
    assert client.get(f"/api/v1/queue-entries/{other_entry}", headers=p1["headers"]).status_code == 404
    assert act(client, other_entry, "cancel", p1["headers"]).status_code == 404

    # The queue's own doctor sees names; another doctor and another hospital see nothing.
    assert entries(client, q, H["doc"])[0]["patient"]["full_name"]
    other_doc = factory.staff(clinic["hospital_id"], "doctor", department_id=clinic["department_id"])
    other_h = auth_headers(client, other_doc["email"])
    assert client.get(f"/api/v1/queues/{q}/entries", headers=other_h).status_code == 404
    assert client.post(f"/api/v1/queues/{q}/call-next", headers=other_h).status_code == 404
    other_hospital = factory.clinic("HOSB")
    assert client.get(f"/api/v1/queues/{q}/entries",
                      headers=auth_headers(client, other_hospital["receptionist"]["email"])).status_code == 404

    lists = client.get(f"/api/v1/hospitals/{clinic['hospital_id']}/queues", headers=other_h).get_json()
    assert lists == []  # other doctor's own (empty) view


def test_queue_summary_counts(client, clinic, H):
    walk_in(client, clinic, H, "One Person")
    walk_in(client, clinic, H, "Two Person")
    [summary] = client.get(f"/api/v1/hospitals/{clinic['hospital_id']}/queues", headers=H["rec"]).get_json()
    assert summary["counts"] == {"waiting": 2}
    assert summary["doctor"]["public_status"] in ("expected", "unavailable")


def test_patient_entry_events_are_sanitised(client, factory, clinic, H):
    p = make_patient(client)
    entry = check_in(client, booked(factory, clinic, p), p["headers"]).get_json()
    events = client.get(f"/api/v1/queue-entries/{entry['id']}/events", headers=p["headers"]).get_json()
    assert events[0]["event_type"] == "checked_in"
    assert events[0]["actor_user_id"] is None and events[0]["details"] == {}


# --- End of day and integrity ----------------------------------------------------------------------


def test_close_past_days(client, app, factory, clinic, H):
    p = make_patient(client)
    appt_id = booked(factory, clinic, p)
    entry = check_in(client, appt_id, H["rec"]).get_json()
    with app.app_context():
        q = db.session.get(Queue, uuid.UUID(entry["queue_id"]))
        q.service_date -= timedelta(days=1)
        db.session.execute(text("UPDATE queue_entries SET service_date = service_date - 1"))
        db.session.commit()
        from careflow.services.queue_service import close_past_days

        assert close_past_days() == 1
    closed = client.get(f"/api/v1/queue-entries/{entry['id']}", headers=H["rec"]).get_json()
    assert closed["status"] == "cancelled"
    assert client.get(f"/api/v1/appointments/{appt_id}", headers=H["rec"]).get_json()["status"] == "no_show"


def test_queue_events_are_append_only(client, app, clinic, H):
    walk_in(client, clinic, H)
    with app.app_context():
        with pytest.raises(DBAPIError, match="append-only"):
            db.session.execute(text("UPDATE queue_events SET reason = 'tampered'"))
        db.session.rollback()


def test_stale_version_rejected(client, clinic, H):
    entry = walk_in(client, clinic, H)
    resp = act(client, entry["id"], "cancel", H["rec"], reason="Testing", expected_version=entry["version"] + 1)
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "stale_version"
