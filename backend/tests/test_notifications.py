from tests.conftest import auth_headers, register_patient, slot_time


def _request(client, clinic, headers):
    return client.post("/api/v1/appointments", headers=headers,
                       json={"doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time()}).get_json()


def test_list_and_mark_read(client, factory):
    clinic = factory.clinic()
    p = register_patient(client)
    headers = auth_headers(client, p["email"])
    _request(client, clinic, headers)
    page = client.get("/api/v1/notifications", headers=headers).get_json()
    assert page["unread_count"] == 1
    nid = page["items"][0]["id"]
    assert page["items"][0]["related_type"] == "appointment"

    read = client.post(f"/api/v1/notifications/{nid}/read", headers=headers)
    assert read.status_code == 200 and read.get_json()["read_at"]
    assert client.get("/api/v1/notifications?unread_only=true", headers=headers).get_json()["items"] == []


def test_cannot_read_someone_elses_notification(client, factory):
    clinic = factory.clinic()
    p1, p2 = register_patient(client), register_patient(client)
    h1 = auth_headers(client, p1["email"])
    _request(client, clinic, h1)
    nid = client.get("/api/v1/notifications", headers=h1).get_json()["items"][0]["id"]
    assert client.post(f"/api/v1/notifications/{nid}/read", headers=auth_headers(client, p2["email"])).status_code == 404
    assert client.get("/api/v1/notifications", headers=auth_headers(client, p2["email"])).get_json()["items"] == []


def test_mark_all_read(client, factory):
    clinic = factory.clinic()
    p = register_patient(client)
    headers = auth_headers(client, p["email"])
    _request(client, clinic, headers)
    assert client.post("/api/v1/notifications/read-all", headers=headers).get_json() == {"marked": 1}
    assert client.get("/api/v1/notifications", headers=headers).get_json()["unread_count"] == 0


def test_notifications_contain_no_clinical_details(client, factory):
    clinic = factory.clinic()
    p = register_patient(client)
    headers = auth_headers(client, p["email"])
    client.post("/api/v1/appointments", headers=headers, json={
        "doctor_id": str(clinic["doctor"]["doctor_id"]), "start_at": slot_time(),
        "reason_for_visit": "Persistent chest pain"})
    body = client.get("/api/v1/notifications", headers=headers).get_json()["items"][0]["body"]
    assert "chest pain" not in body.lower()


def test_call_next_sends_sms_and_alerts_next_in_line(client, factory):
    from datetime import UTC, datetime, time, timedelta
    from careflow.services import email_service, sms_service

    sms_mock = sms_service.get_mock_provider()
    email_mock = email_service.get_mock_provider()
    sms_mock.clear()
    email_mock.clear()

    hid = factory.hospital("HOSNTF")
    dept = factory.department(hid)
    doc_data = factory.staff(hid, "doctor", department_id=dept)
    factory.schedule(hid, doc_data["doctor_id"], start=time(0, 0), end=time(23, 45))
    rec_data = factory.staff(hid, "receptionist")

    h_doc = auth_headers(client, doc_data["email"])
    h_rec = auth_headers(client, rec_data["email"])

    # Doctor becomes available
    client.post("/api/v1/doctors/me/status", json={"status": "checked_in"}, headers=h_doc)
    client.post("/api/v1/doctors/me/status", json={"status": "available"}, headers=h_doc)

    # Register two patients with phone numbers
    p1 = register_patient(client, email="p1@example.com", phone="+15551234567")
    p2 = register_patient(client, email="p2@example.com", phone="+15559876543")

    h_p1 = auth_headers(client, p1["email"])
    h_p2 = auth_headers(client, p2["email"])

    # Book appointments
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    appt1 = factory.approved_appointment(hid, doc_data["doctor_id"], p1["patient_id"], start_at=now)
    appt2 = factory.approved_appointment(hid, doc_data["doctor_id"], p2["patient_id"], start_at=now + timedelta(minutes=15))

    # Check in
    client.post(f"/api/v1/appointments/{appt1}/check-in", headers=h_p1)
    entry2_resp = client.post(f"/api/v1/appointments/{appt2}/check-in", headers=h_p2)
    queue_id = entry2_resp.get_json()["queue_id"]

    sms_mock.clear()
    email_mock.clear()

    # --- First Call ---
    call_resp = client.post(f"/api/v1/queues/{queue_id}/call-next", headers=h_doc)
    assert call_resp.status_code == 200
    called_entry = call_resp.get_json()

    # 1. Patient 1 (called first time): receives SMS, Email, and In-App notification
    p1_sms = [m for m in sms_mock.sent if m.to_phone == "+15551234567"]
    assert len(p1_sms) == 1
    assert "turn" in p1_sms[0].message.lower()

    p1_emails = [e for e in email_mock.sent if e.to_email == "p1@example.com"]
    assert len(p1_emails) == 1
    assert "turn" in p1_emails[0].subject.lower()

    # 2. Patient 2 ("next in line" heads-up): receives SMS, Email, and In-App notification
    p2_sms = [m for m in sms_mock.sent if m.to_phone == "+15559876543"]
    assert len(p2_sms) == 1
    assert "next in line" in p2_sms[0].message.lower()

    p2_emails = [e for e in email_mock.sent if e.to_email == "p2@example.com"]
    assert len(p2_emails) == 1
    assert "next in line" in p2_emails[0].subject.lower()

    # Verify in-app notifications
    notifs_p1 = client.get("/api/v1/notifications", headers=h_p1).get_json()["items"]
    assert any("turn" in n["title"].lower() for n in notifs_p1)

    notifs_p2 = client.get("/api/v1/notifications", headers=h_p2).get_json()["items"]
    assert any("next in line" in n["title"].lower() for n in notifs_p2)

    # --- Second Call (Recall) ---
    # Doctor calls patient 1 a second time
    sms_mock.clear()
    email_mock.clear()

    recall_resp = client.post(f"/api/v1/queue-entries/{called_entry['id']}/recall",
                              json={"expected_version": called_entry["version"]},
                              headers=h_doc)
    assert recall_resp.status_code == 200

    # SMS is sent ONCE only: NO new SMS on recall
    p1_recall_sms = [m for m in sms_mock.sent if m.to_phone == "+15551234567"]
    assert len(p1_recall_sms) == 0

    # Email IS delivered on recall
    p1_recall_email = [e for e in email_mock.sent if e.to_email == "p1@example.com"]
    assert len(p1_recall_email) == 1
    assert "called again" in p1_recall_email[0].subject.lower()


def test_walk_in_patient_receives_sms(client, factory):
    from datetime import time
    from careflow.services import sms_service

    sms_mock = sms_service.get_mock_provider()
    sms_mock.clear()

    hid = factory.hospital("HOSWLK")
    dept = factory.department(hid)
    doc_data = factory.staff(hid, "doctor", department_id=dept)
    factory.schedule(hid, doc_data["doctor_id"], start=time(0, 0), end=time(23, 45))
    rec_data = factory.staff(hid, "receptionist")

    h_doc = auth_headers(client, doc_data["email"])
    h_rec = auth_headers(client, rec_data["email"])

    client.post("/api/v1/doctors/me/status", json={"status": "checked_in"}, headers=h_doc)
    client.post("/api/v1/doctors/me/status", json={"status": "available"}, headers=h_doc)

    # Reception registers a walk-in patient with a phone number
    pat = client.post(f"/api/v1/hospitals/{hid}/patients",
                      json={"full_name": "Walkin Phone Patient", "phone": "+15557778888"},
                      headers=h_rec).get_json()

    # Reception adds walk-in to queue
    resp = client.post(f"/api/v1/hospitals/{hid}/queue/walk-ins",
                       json={"patient_id": pat["id"], "doctor_id": str(doc_data["doctor_id"])},
                       headers=h_rec)
    assert resp.status_code == 201
    queue_id = resp.get_json()["queue_id"]

    sms_mock.clear()

    # Call patient
    call_resp = client.post(f"/api/v1/queues/{queue_id}/call-next", headers=h_doc)
    assert call_resp.status_code == 200

    # Walk-in without user account still received SMS
    walkin_sms = [m for m in sms_mock.sent if m.to_phone == "+15557778888"]
    assert len(walkin_sms) == 1
    assert "turn" in walkin_sms[0].message.lower()

