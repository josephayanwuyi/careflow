from tests.conftest import auth_headers


def test_staff_and_report_api(client):
    # Register hospital admin
    resp = client.post(
        "/api/v1/auth/register-hospital",
        json={
            "hospital_name": "Metro Health Clinic",
            "hospital_code": "METROHLTH",
            "hospital_slug": "metro-health",
            "timezone": "Africa/Lagos",
            "admin_name": "Chief Medical Officer",
            "admin_email": "cmo@metrohealth.test",
            "admin_password": "SecurePassword987",
            "client_type": "desktop",
        },
    )
    assert resp.status_code == 201
    data = resp.get_json()
    token = data["token"]["access_token"]
    hospital_id = data["hospital_id"]

    # 1. Admin lists staff via API
    staff_resp = client.get(
        f"/api/v1/hospitals/{hospital_id}/staff",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert staff_resp.status_code == 200
    staff = staff_resp.get_json()
    assert any(s["email"] == "cmo@metrohealth.test" for s in staff)

    # 2. Admin adds a receptionist directly via API (no technician needed!)
    create_resp = client.post(
        f"/api/v1/hospitals/{hospital_id}/staff",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "full_name": "Amaka Frontdesk",
            "email": "amaka.desk@metrohealth.test",
            "temporary_password": "TemporaryPassword123",
            "roles": ["receptionist"],
            "phone": "+2348011223344",
        },
    )
    assert create_resp.status_code == 201
    created_user = create_resp.get_json()
    assert created_user["email"] == "amaka.desk@metrohealth.test"
    assert "receptionist" in created_user["roles"]

    # 3. Verify the receptionist can now sign in immediately
    login_resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": "amaka.desk@metrohealth.test",
            "password": "TemporaryPassword123",
            "client_type": "desktop",
        },
    )
    assert login_resp.status_code == 200
    receptionist_data = login_resp.get_json()
    assert receptionist_data["user"]["email"] == "amaka.desk@metrohealth.test"

    # 4. Admin queries notification reports
    report_resp = client.get(
        f"/api/v1/hospitals/{hospital_id}/notifications/report",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert report_resp.status_code == 200
    rep = report_resp.get_json()
    assert "sms_sent" in rep
    assert "email_sent" in rep
    assert "push_sent" in rep
