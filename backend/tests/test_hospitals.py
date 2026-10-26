import uuid

from tests.conftest import PASSWORD, auth_headers, login


def test_public_hospital_and_department_listing(client, factory):
    hid = factory.hospital("HOSA", "Alpha Hospital")
    factory.department(hid, "Paediatrics")
    hospitals = client.get("/api/v1/hospitals").get_json()
    assert [h["name"] for h in hospitals] == ["Alpha Hospital"]
    depts = client.get(f"/api/v1/hospitals/{hid}/departments").get_json()
    assert [d["name"] for d in depts] == ["Paediatrics"]


def test_unknown_hospital_is_404(client):
    resp = client.get(f"/api/v1/hospitals/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_doctor_listing_exposes_no_contact_details(client, factory):
    hid = factory.hospital()
    dept = factory.department(hid)
    factory.staff(hid, "doctor", email="doc@example.test", department_id=dept)
    [doc] = client.get(f"/api/v1/hospitals/{hid}/doctors").get_json()
    assert doc["department_name"] == "General Outpatient"
    serialized = str(doc)
    assert "doc@example.test" not in serialized and "email" not in doc and "phone" not in doc
    assert client.get(f"/api/v1/doctors/{doc['id']}").status_code == 200
    other_dept = factory.department(hid, "Other")
    assert client.get(f"/api/v1/hospitals/{hid}/doctors?department_id={other_dept}").get_json() == []


def test_only_admin_can_create_department(client, factory):
    hid = factory.hospital()
    admin = factory.staff(hid, "hospital_admin")
    receptionist = factory.staff(hid, "receptionist")
    body = {"name": "Cardiology"}
    resp = client.post(f"/api/v1/hospitals/{hid}/departments", json=body, headers=auth_headers(client, receptionist["email"]))
    assert resp.status_code == 403
    resp = client.post(f"/api/v1/hospitals/{hid}/departments", json=body, headers=auth_headers(client, admin["email"]))
    assert resp.status_code == 201
    dup = client.post(f"/api/v1/hospitals/{hid}/departments", json=body, headers=auth_headers(client, admin["email"]))
    assert dup.status_code == 409


def test_admin_cannot_manage_another_hospital(client, factory):
    a, b = factory.hospital("HOSA"), factory.hospital("HOSB")
    admin_a = factory.staff(a, "hospital_admin")
    resp = client.post(f"/api/v1/hospitals/{b}/departments", json={"name": "X-ray"},
                       headers=auth_headers(client, admin_a["email"]))
    assert resp.status_code == 403


def test_admin_creates_doctor_account(client, factory):
    hid = factory.hospital()
    dept = factory.department(hid)
    admin = factory.staff(hid, "hospital_admin")
    resp = client.post(f"/api/v1/hospitals/{hid}/staff", headers=auth_headers(client, admin["email"]), json={
        "email": "new.doc@example.test", "full_name": "New Doctor", "roles": ["doctor"],
        "temporary_password": PASSWORD, "department_id": str(dept), "title": "Dr.",
    })
    assert resp.status_code == 201, resp.get_json()
    data = login(client, "new.doc@example.test")
    [m] = data["user"]["memberships"]
    assert m["roles"] == ["doctor"] and m["doctor_id"]


def test_doctor_department_must_belong_to_hospital(client, factory):
    a, b = factory.hospital("HOSA"), factory.hospital("HOSB")
    dept_b = factory.department(b)
    admin_a = factory.staff(a, "hospital_admin")
    resp = client.post(f"/api/v1/hospitals/{a}/staff", headers=auth_headers(client, admin_a["email"]), json={
        "email": "x.doc@example.test", "full_name": "X Doctor", "roles": ["doctor"],
        "temporary_password": PASSWORD, "department_id": str(dept_b),
    })
    assert resp.status_code == 404


def test_staff_role_values_validated(client, factory):
    hid = factory.hospital()
    admin = factory.staff(hid, "hospital_admin")
    resp = client.post(f"/api/v1/hospitals/{hid}/staff", headers=auth_headers(client, admin["email"]), json={
        "email": "x@example.test", "full_name": "X Person", "roles": ["superuser"], "temporary_password": PASSWORD,
    })
    assert resp.status_code == 422
