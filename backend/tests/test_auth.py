from datetime import timedelta

from careflow.extensions import db
from careflow.models import AuthSession, User
from careflow.models.base import utcnow
from tests.conftest import PASSWORD, auth_headers, login, register_patient


def test_register_creates_patient_account(client):
    data = register_patient(client, email="Ada@Example.test", phone="0803 123 4567")
    assert data["email"] == "ada@example.test"
    assert data["phone"] == "+2348031234567"
    assert data["patient_id"]
    assert "password" not in data and "password_hash" not in data


def test_register_rejects_duplicate_email_case_insensitive(client):
    register_patient(client, email="dup@example.test")
    resp = client.post("/api/v1/auth/register",
                       json={"email": "DUP@example.test", "password": PASSWORD, "full_name": "Other Person"})
    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "email_taken"


def test_register_validates_input(client):
    resp = client.post("/api/v1/auth/register", json={"email": "not-an-email", "password": "short", "full_name": "A"})
    assert resp.status_code == 422
    details = resp.get_json()["error"]["details"]["json"]
    assert {"email", "password", "full_name"} <= set(details)


def test_register_rejects_unknown_fields(client):
    resp = client.post("/api/v1/auth/register", json={
        "email": "x@example.test", "password": PASSWORD, "full_name": "Some One", "role": "hospital_admin"})
    assert resp.status_code == 422


def test_login_returns_tokens_and_roles(client, factory):
    hid = factory.hospital()
    staff = factory.staff(hid, "receptionist")
    data = login(client, staff["email"])
    assert data["token_type"] == "Bearer"
    assert data["access_token"] and data["refresh_token"]
    [membership] = data["user"]["memberships"]
    assert membership["roles"] == ["receptionist"]
    assert "patients.register" in membership["permissions"]


def test_login_failure_is_generic(client, factory):
    hid = factory.hospital()
    staff = factory.staff(hid, "receptionist")
    wrong_pw = client.post("/api/v1/auth/login", json={"email": staff["email"], "password": "wrong-password"})
    no_user = client.post("/api/v1/auth/login", json={"email": "ghost@example.test", "password": "wrong-password"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.get_json()["error"]["message"] == no_user.get_json()["error"]["message"]


def test_account_locks_after_repeated_failures(client, app, factory):
    hid = factory.hospital()
    staff = factory.staff(hid, "receptionist")
    for _ in range(app.config["LOGIN_MAX_FAILURES"]):
        client.post("/api/v1/auth/login", json={"email": staff["email"], "password": "wrong-password"})
    # Correct password is refused while locked.
    resp = client.post("/api/v1/auth/login", json={"email": staff["email"], "password": PASSWORD})
    assert resp.status_code == 401
    with app.app_context():
        user = db.session.get(User, staff["user_id"])
        assert user.locked_until is not None
        user.locked_until = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert client.post("/api/v1/auth/login", json={"email": staff["email"], "password": PASSWORD}).status_code == 200


def test_me_requires_token(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "token_missing"


def test_garbage_token_rejected(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "token_invalid"


def test_refresh_rotates_and_detects_reuse(client):
    p = register_patient(client)
    tokens = login(client, p["email"])
    first = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    new = first.get_json()
    assert new["refresh_token"] != tokens["refresh_token"]

    # Re-using the old refresh token is treated as theft: the whole session is revoked.
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code == 401
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 401
    assert me.get_json()["error"]["code"] == "session_ended"


def test_logout_ends_access_immediately(client):
    p = register_patient(client)
    tokens = login(client, p["email"])
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_expired_session_is_rejected(client, app):
    p = register_patient(client)
    tokens = login(client, p["email"])
    with app.app_context():
        for s in db.session.query(AuthSession).all():
            s.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_deactivated_user_loses_access(client, app):
    p = register_patient(client)
    headers = auth_headers(client, p["email"])
    with app.app_context():
        db.session.get(User, p["id"]).is_active = False
        db.session.commit()
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_change_password_ends_other_sessions(client):
    p = register_patient(client)
    other = auth_headers(client, p["email"])
    current = auth_headers(client, p["email"])
    resp = client.post("/api/v1/auth/change-password", headers=current,
                       json={"current_password": PASSWORD, "new_password": "a-brand-new-password"})
    assert resp.status_code == 204
    assert client.get("/api/v1/auth/me", headers=current).status_code == 200
    assert client.get("/api/v1/auth/me", headers=other).status_code == 401
    login(client, p["email"], "a-brand-new-password")


def test_change_password_requires_current_password(client):
    p = register_patient(client)
    headers = auth_headers(client, p["email"])
    resp = client.post("/api/v1/auth/change-password", headers=headers,
                       json={"current_password": "wrong-password", "new_password": "a-brand-new-password"})
    assert resp.status_code == 401


def test_passwords_are_hashed_with_argon2(client, app):
    p = register_patient(client)
    with app.app_context():
        user = db.session.get(User, p["id"])
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash
