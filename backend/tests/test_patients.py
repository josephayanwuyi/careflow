import uuid

from tests.conftest import auth_headers, register_patient

WALK_IN = {"full_name": "Synthetic Walkin", "phone": "08031112222", "date_of_birth": "1992-04-01", "sex": "female"}


def _register(client, hid, headers, **overrides):
    return client.post(f"/api/v1/hospitals/{hid}/patients", json={**WALK_IN, **overrides}, headers=headers)


def test_receptionist_registers_walk_in(client, factory):
    hid = factory.hospital("HOSA")
    rec = factory.staff(hid, "receptionist")
    resp = _register(client, hid, auth_headers(client, rec["email"]))
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    assert data["hospital_patient_number"] == "HOSA-000001"
    assert data["phone"] == "+2348031112222"
    assert data["has_online_account"] is False


def test_duplicate_detection_requires_confirmation_and_reason(client, factory):
    hid = factory.hospital()
    headers = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    assert _register(client, hid, headers).status_code == 201

    dup = _register(client, hid, headers, full_name="Someone Else")  # same phone
    assert dup.status_code == 409
    err = dup.get_json()["error"]
    assert err["code"] == "possible_duplicate"
    assert err["details"]["candidates"][0]["hospital_patient_number"].endswith("000001")
    assert "phone" not in err["details"]["candidates"][0]  # only last 4 digits

    no_reason = _register(client, hid, headers, full_name="Someone Else", confirm_not_duplicate=True)
    assert no_reason.status_code == 422
    ok = _register(client, hid, headers, full_name="Someone Else", confirm_not_duplicate=True,
                   duplicate_override_reason="Different person sharing a family phone")
    assert ok.status_code == 201
    assert ok.get_json()["hospital_patient_number"].endswith("000002")


def test_same_name_and_dob_flags_duplicate(client, factory):
    hid = factory.hospital()
    headers = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    _register(client, hid, headers)
    resp = _register(client, hid, headers, phone=None, full_name="  synthetic   WALKIN ")
    assert resp.status_code == 409


def test_doctor_cannot_register_or_search_patients(client, factory):
    hid = factory.hospital()
    headers = auth_headers(client, factory.staff(hid, "doctor")["email"])
    assert _register(client, hid, headers).status_code == 403
    assert client.get(f"/api/v1/hospitals/{hid}/patients?q=Synthetic", headers=headers).status_code == 403


def test_patient_cannot_use_staff_endpoints(client, factory):
    hid = factory.hospital()
    p = register_patient(client)
    headers = auth_headers(client, p["email"])
    assert _register(client, hid, headers).status_code == 403
    assert client.get(f"/api/v1/hospitals/{hid}/patients?q=Synthetic", headers=headers).status_code == 403


def test_search_by_number_phone_and_name(client, factory):
    hid = factory.hospital("HOSA")
    headers = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    _register(client, hid, headers)
    for q in ("hosa-000001", "0803 111 2222", "walkin"):
        resp = client.get(f"/api/v1/hospitals/{hid}/patients", query_string={"q": q}, headers=headers)
        assert resp.status_code == 200
        assert [p["hospital_patient_number"] for p in resp.get_json()] == ["HOSA-000001"], q
    resp = client.get(f"/api/v1/hospitals/{hid}/patients", query_string={"q": "%%%"}, headers=headers)
    assert resp.get_json() == []
    assert client.get(f"/api/v1/hospitals/{hid}/patients?q=ab", headers=headers).status_code == 422


def test_cross_hospital_isolation(client, factory):
    a, b = factory.hospital("HOSA"), factory.hospital("HOSB")
    headers_a = auth_headers(client, factory.staff(a, "receptionist")["email"])
    headers_b = auth_headers(client, factory.staff(b, "receptionist")["email"])
    patient_id = _register(client, a, headers_a).get_json()["id"]

    # Hospital B staff cannot use hospital A's routes...
    assert client.get(f"/api/v1/hospitals/{a}/patients/{patient_id}", headers=headers_b).status_code == 403
    # ...and the patient does not exist from hospital B's point of view.
    assert client.get(f"/api/v1/hospitals/{b}/patients/{patient_id}", headers=headers_b).status_code == 404
    resp = client.get(f"/api/v1/hospitals/{b}/patients", query_string={"q": "Walkin"}, headers=headers_b)
    assert resp.get_json() == []
    assert client.get(f"/api/v1/hospitals/{a}/patients/{patient_id}", headers=headers_a).status_code == 200


def test_unknown_patient_is_404(client, factory):
    hid = factory.hospital()
    headers = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    assert client.get(f"/api/v1/hospitals/{hid}/patients/{uuid.uuid4()}", headers=headers).status_code == 404


def test_patient_reads_and_updates_own_profile(client):
    p = register_patient(client, phone="08030000001")
    headers = auth_headers(client, p["email"])
    me = client.get("/api/v1/patients/me", headers=headers).get_json()
    assert me["id"] == p["patient_id"]
    resp = client.patch("/api/v1/patients/me", json={"full_name": "Renamed Patient", "sex": "male"}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["full_name"] == "Renamed Patient"
    assert resp.get_json()["phone"] == "+2348030000001"


def test_patient_cannot_take_another_accounts_phone(client):
    register_patient(client, phone="08030000001")
    p2 = register_patient(client)
    resp = client.patch("/api/v1/patients/me", json={"phone": "+2348030000001"}, headers=auth_headers(client, p2["email"]))
    assert resp.status_code == 409


def test_staff_without_patient_profile_cannot_use_patient_endpoints(client, factory):
    hid = factory.hospital()
    headers = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    assert client.get("/api/v1/patients/me", headers=headers).status_code == 403
