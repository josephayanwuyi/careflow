"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from datetime import timedelta


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _csv(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_config(env: str | None = None) -> dict:
    env = env or os.environ.get("APP_ENV", "development")
    if env not in {"development", "testing", "production"}:
        raise ConfigError(f"Unknown APP_ENV {env!r}")

    db_url_var = "TEST_DATABASE_URL" if env == "testing" else "DATABASE_URL"
    secret_key = _require("SECRET_KEY")
    jwt_secret = _require("JWT_SECRET_KEY")
    if env == "production" and (len(secret_key) < 32 or len(jwt_secret) < 32):
        raise ConfigError("SECRET_KEY and JWT_SECRET_KEY must be at least 32 characters in production")

    return {
        "APP_ENV": env,
        "TESTING": env == "testing",
        "SECRET_KEY": secret_key,
        "SQLALCHEMY_DATABASE_URI": _require(db_url_var),
        # Sessions run in UTC so every timestamp leaves the API as UTC regardless of server settings.
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True, "connect_args": {"options": "-c timezone=UTC"}},
        # Auth
        "JWT_SECRET_KEY": jwt_secret,
        "JWT_ACCESS_TOKEN_EXPIRES": timedelta(minutes=_int("ACCESS_TOKEN_MINUTES", 15)),
        "JWT_TOKEN_LOCATION": ["headers"],
        "JWT_ALGORITHM": "HS256",
        "SESSION_MAX_AGE": timedelta(hours=_int("SESSION_MAX_HOURS", 12)),
        "LOGIN_MAX_FAILURES": _int("LOGIN_MAX_FAILURES", 5),
        "LOGIN_LOCKOUT_MINUTES": _int("LOGIN_LOCKOUT_MINUTES", 15),
        # Web refresh cookie: Secure (HTTPS-only) everywhere except local development over http.
        "COOKIE_SECURE": os.environ.get("COOKIE_SECURE", "true" if env == "production" else "false").lower() == "true",
        # Rate limiting
        "RATELIMIT_ENABLED": os.environ.get("RATELIMIT_ENABLED", "true").lower() != "false",
        "RATELIMIT_STORAGE_URI": os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_HEADERS_ENABLED": True,
        # CORS
        "CORS_ORIGINS": _csv("CORS_ORIGINS"),
        # Logging
        "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
        # OpenAPI
        "API_TITLE": "CareFlow API",
        "API_VERSION": "v1",
        "OPENAPI_VERSION": "3.0.3",
        "OPENAPI_URL_PREFIX": "/api",
        "OPENAPI_JSON_PATH": "openapi.json",
        "OPENAPI_SWAGGER_UI_PATH": "/docs",
        "OPENAPI_SWAGGER_UI_URL": "https://cdn.jsdelivr.net/npm/swagger-ui-dist/",
        "API_SPEC_OPTIONS": {
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
                }
            },
        },
        "JSON_SORT_KEYS": False,
    }
