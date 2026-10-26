"""Cross-cutting behaviour: error format, headers, health, OpenAPI, rate limiting."""

from careflow.extensions import limiter


def test_error_format_and_request_id(client):
    resp = client.get("/api/v1/hospitals/not-a-uuid", headers={"X-Request-ID": "test-request-1234"})
    assert resp.status_code == 404
    body = resp.get_json()
    assert set(body["error"]) == {"code", "message", "details", "request_id"}
    assert body["error"]["request_id"] == "test-request-1234"
    assert resp.headers["X-Request-ID"] == "test-request-1234"


def test_invalid_request_id_is_replaced(client):
    resp = client.get("/health/live", headers={"X-Request-ID": "bad id with spaces"})
    assert resp.headers["X-Request-ID"] != "bad id with spaces"


def test_security_headers(client):
    resp = client.get("/api/v1/hospitals")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"


def test_method_not_allowed_uses_error_format(client):
    resp = client.delete("/api/v1/hospitals")
    assert resp.status_code == 405
    assert resp.get_json()["error"]["code"] == "method_not_allowed"


def test_malformed_json_is_rejected(client):
    resp = client.post("/api/v1/auth/login", data="{not json", content_type="application/json")
    assert resp.status_code in (400, 422)
    assert "error" in resp.get_json()


def test_health_endpoints(client):
    assert client.get("/health/live").get_json() == {"status": "ok"}
    assert client.get("/health/ready").get_json() == {"status": "ok", "database": "ok"}


def test_openapi_document(client):
    spec = client.get("/api/openapi.json").get_json()
    assert spec["info"]["title"] == "CareFlow API"
    assert "/api/v1/auth/login" in spec["paths"]
    assert "bearerAuth" in spec["components"]["securitySchemes"]


def test_cors_allows_only_configured_origins(client, app):
    allowed = app.config["CORS_ORIGINS"][0]
    ok = client.get("/api/v1/hospitals", headers={"Origin": allowed})
    assert ok.headers.get("Access-Control-Allow-Origin") == allowed
    evil = client.get("/api/v1/hospitals", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in evil.headers


def test_login_is_rate_limited(client):
    limiter.enabled = True
    limiter.reset()
    try:
        statuses = [client.post("/api/v1/auth/login", json={"email": "x@example.test", "password": "wrong-password"}).status_code
                    for _ in range(11)]
        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429
        resp = client.post("/api/v1/auth/login", json={"email": "x@example.test", "password": "wrong-password"})
        assert resp.get_json()["error"]["code"] == "rate_limited"
    finally:
        limiter.reset()
