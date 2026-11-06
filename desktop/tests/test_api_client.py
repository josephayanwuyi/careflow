"""ApiClient behaviour against a mock HTTP transport (no server needed)."""

import json
import threading

import httpx
import pytest

from careflow_desktop.api.client import ApiClient
from careflow_desktop.api.errors import ApiError, NetworkError, SessionExpired

USER = {"id": "u1", "full_name": "Desk User", "memberships": []}


def err(status, code, message="Problem", details=None):
    return httpx.Response(status, json={"error": {"code": code, "message": message, "details": details,
                                                  "request_id": "req-1"}})


class Server:
    """Scriptable fake API: routes (method, path) to handler functions, records requests."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes = {}
        self.refresh_calls = 0
        self.lock = threading.Lock()
        self.valid_access = {"access-1"}
        self.routes[("POST", "/api/v1/auth/login")] = lambda r: httpx.Response(200, json={
            "access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 900, "token_type": "Bearer",
            "user": USER})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return err(404, "not_found")
        return handler(request)

    def client(self, **kw) -> ApiClient:
        return ApiClient("http://test", transport=httpx.MockTransport(self), **kw)


def bearer(request):
    return request.headers.get("Authorization", "").removeprefix("Bearer ")


def test_login_stores_tokens_and_identifies_desktop():
    server = Server()
    client = server.client()
    assert client.login("a@example.test", "pw") == USER
    assert client.is_authenticated
    body = json.loads(server.requests[0].content)
    assert body["client_type"] == "desktop"
    assert server.requests[0].headers["User-Agent"].startswith("CareFlowDesktop/")


def test_unauthenticated_calls_raise_session_expired():
    with pytest.raises(SessionExpired):
        Server().client().me()


def test_expired_token_is_refreshed_once_and_request_retried():
    server = Server()

    def me(request):
        if bearer(request) in server.valid_access:
            return httpx.Response(200, json=USER)
        return err(401, "token_expired")

    def refresh(request):
        server.refresh_calls += 1
        server.valid_access = {"access-2"}
        return httpx.Response(200, json={"access_token": "access-2", "refresh_token": "refresh-2",
                                         "expires_in": 900, "user": USER})

    server.routes[("GET", "/api/v1/auth/me")] = me
    server.routes[("POST", "/api/v1/auth/refresh")] = refresh
    client = server.client()
    client.login("a@example.test", "pw")
    server.valid_access = set()  # access-1 has expired
    assert client.me() == USER
    assert server.refresh_calls == 1
    assert bearer(server.requests[-1]) == "access-2"


def test_concurrent_expiry_refreshes_only_once():
    server = Server()
    gate = threading.Barrier(4)

    def me(request):
        return httpx.Response(200, json=USER) if bearer(request) in server.valid_access else err(401, "token_expired")

    def refresh(request):
        with server.lock:
            server.refresh_calls += 1
        server.valid_access = {"access-2"}
        return httpx.Response(200, json={"access_token": "access-2", "refresh_token": "refresh-2", "user": USER})

    server.routes[("GET", "/api/v1/auth/me")] = me
    server.routes[("POST", "/api/v1/auth/refresh")] = refresh
    client = server.client()
    client.login("a@example.test", "pw")
    server.valid_access = set()
    results = []

    def worker():
        gate.wait()
        results.append(client.me())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [USER] * 4
    assert server.refresh_calls == 1


def test_failed_refresh_ends_session_and_notifies():
    server = Server()
    server.routes[("GET", "/api/v1/auth/me")] = lambda r: err(401, "token_expired")
    server.routes[("POST", "/api/v1/auth/refresh")] = lambda r: err(401, "session_ended")
    client = server.client()
    expired = []
    client.on_session_expired = lambda: expired.append(True)
    client.login("a@example.test", "pw")
    with pytest.raises(SessionExpired):
        client.me()
    assert expired == [True] and not client.is_authenticated


def test_revoked_session_is_not_refreshed():
    server = Server()
    server.routes[("GET", "/api/v1/auth/me")] = lambda r: err(401, "session_ended")
    client = server.client()
    client.login("a@example.test", "pw")
    with pytest.raises(SessionExpired):
        client.me()
    assert not any(r.url.path.endswith("/refresh") for r in server.requests)


def test_api_errors_carry_code_and_readable_details():
    server = Server()
    server.routes[("POST", "/api/v1/appointments/a1/reject")] = lambda r: err(
        422, "validation_failed", "The request contains invalid data.", {"json": {"reason": ["A reason is required."]}})
    client = server.client()
    client.login("a@example.test", "pw")
    with pytest.raises(ApiError) as exc:
        client.appointment_action("a1", "reject")
    assert exc.value.code == "validation_failed" and exc.value.status == 422
    assert "reason: A reason is required." in exc.value.user_message


def test_server_error_without_json_body():
    server = Server()
    server.routes[("GET", "/api/v1/auth/me")] = lambda r: httpx.Response(502, text="Bad gateway")
    client = server.client()
    client.login("a@example.test", "pw")
    with pytest.raises(ApiError) as exc:
        client.me()
    assert exc.value.status == 502 and "server had a problem" in exc.value.message


def test_network_failures_become_network_errors():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    client = ApiClient("http://test", transport=httpx.MockTransport(down))
    with pytest.raises(NetworkError) as exc:
        client.login("a@example.test", "pw")
    assert "Cannot reach" in exc.value.message

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = ApiClient("http://test", transport=httpx.MockTransport(slow))
    with pytest.raises(NetworkError) as exc:
        client.login("a@example.test", "pw")
    assert "too long" in exc.value.message


def test_empty_filters_are_not_sent():
    server = Server()
    server.routes[("GET", "/api/v1/hospitals/h1/appointments")] = lambda r: httpx.Response(
        200, json={"items": [], "next_cursor": None})
    client = server.client()
    client.login("a@example.test", "pw")
    client.appointments("h1", status=["requested", "approved"], doctor_id=None, date_from="")
    params = dict(server.requests[-1].url.params)
    assert params == {"status": "requested,approved"}


def test_logout_forgets_tokens_even_if_server_unreachable():
    server = Server()
    server.routes[("POST", "/api/v1/auth/logout")] = lambda r: err(503, "unavailable")
    client = server.client()
    client.login("a@example.test", "pw")
    client.logout()
    assert not client.is_authenticated
