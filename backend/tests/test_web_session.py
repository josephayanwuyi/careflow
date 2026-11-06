"""Browser sessions: refresh token in an HttpOnly cookie, protected by a double-submit CSRF token."""

from careflow.api.auth import CSRF_COOKIE, REFRESH_COOKIE
from tests.conftest import PASSWORD, register_patient


def web_login(client, email):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD, "client_type": "web"})
    assert resp.status_code == 200, resp.get_json()
    return resp


def csrf(client):
    return client.get_cookie(CSRF_COOKIE, path="/").value


def test_web_login_sets_cookies_and_hides_refresh_token(client):
    p = register_patient(client)
    resp = web_login(client, p["email"])
    assert resp.get_json()["refresh_token"] is None
    cookies = resp.headers.getlist("Set-Cookie")
    refresh_cookie = next(c for c in cookies if c.startswith(f"{REFRESH_COOKIE}="))
    assert "HttpOnly" in refresh_cookie and "SameSite=Strict" in refresh_cookie
    assert "Path=/api/v1/auth" in refresh_cookie
    csrf_cookie = next(c for c in cookies if c.startswith(f"{CSRF_COOKIE}="))
    assert "HttpOnly" not in csrf_cookie  # the page must be able to read it


def test_cookie_refresh_requires_matching_csrf_header(client):
    p = register_patient(client)
    web_login(client, p["email"])
    missing = client.post("/api/v1/auth/refresh", json={})
    assert missing.status_code == 403 and missing.get_json()["error"]["code"] == "csrf_failed"
    wrong = client.post("/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": "forged"})
    assert wrong.status_code == 403

    ok = client.post("/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": csrf(client)})
    assert ok.status_code == 200 and ok.get_json()["refresh_token"] is None
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ok.get_json()['access_token']}"})
    assert me.status_code == 200


def test_cookie_rotates_on_refresh(client):
    p = register_patient(client)
    web_login(client, p["email"])
    first = client.get_cookie(REFRESH_COOKIE, path="/api/v1/auth").value
    client.post("/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": csrf(client)})
    second = client.get_cookie(REFRESH_COOKIE, path="/api/v1/auth").value
    assert first != second


def test_refresh_without_cookie_is_401(client):
    resp = client.post("/api/v1/auth/refresh", json={})
    assert resp.status_code == 401 and resp.get_json()["error"]["code"] == "session_ended"


def test_logout_clears_cookies_and_ends_session(client):
    p = register_patient(client)
    token = web_login(client, p["email"]).get_json()["access_token"]
    token_csrf = csrf(client)
    resp = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    assert client.get_cookie(REFRESH_COOKIE, path="/api/v1/auth") is None
    again = client.post("/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": token_csrf})
    assert again.status_code in (401, 403)


def test_desktop_login_still_returns_refresh_token(client):
    p = register_patient(client)
    resp = client.post("/api/v1/auth/login", json={"email": p["email"], "password": PASSWORD, "client_type": "desktop"})
    assert resp.get_json()["refresh_token"]
    assert not any(c.startswith(REFRESH_COOKIE) for c in resp.headers.getlist("Set-Cookie"))


def test_secure_flag_follows_config(app, client):
    p = register_patient(client)
    app.config["COOKIE_SECURE"] = True
    try:
        cookies = web_login(client, p["email"]).headers.getlist("Set-Cookie")
    finally:
        app.config["COOKIE_SECURE"] = False
    assert all("Secure" in c for c in cookies)


def test_doctor_reads_own_status(client, factory):
    from tests.conftest import auth_headers

    hid = factory.hospital()
    doctor = factory.staff(hid, "doctor")
    headers = auth_headers(client, doctor["email"])
    data = client.get("/api/v1/doctors/me/status", headers=headers).get_json()
    assert data["doctor_id"] == str(doctor["doctor_id"]) and data["status"] == "off_duty"
    rec = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    assert client.get("/api/v1/doctors/me/status", headers=rec).status_code == 403
