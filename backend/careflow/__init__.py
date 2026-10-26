"""CareFlow backend application factory."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from flask import Flask, g, request

from careflow.config import build_config
from careflow.errors import AppError, error_response
from careflow.extensions import api as rest_api
from careflow.extensions import cors, db, jwt, limiter, migrate
from careflow.logging_setup import configure_logging

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
log = structlog.get_logger("careflow")


def create_app(env: str | None = None, overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(build_config(env))
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    if overrides:
        app.config.update(overrides)

    configure_logging(app.config["LOG_LEVEL"])

    from careflow import models  # noqa: F401  (register models with SQLAlchemy metadata)
    import careflow.security.auth  # noqa: F401  (register JWT error loaders)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    limiter.init_app(app)
    if app.config["CORS_ORIGINS"]:
        cors.init_app(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
                      expose_headers=["X-Request-ID"], max_age=600)
    rest_api.init_app(app)

    from careflow.api import audit, auth, health, hospitals, patients

    rest_api.register_blueprint(auth.blp)
    rest_api.register_blueprint(hospitals.blp)
    rest_api.register_blueprint(hospitals.doctors_blp)
    rest_api.register_blueprint(patients.blp)
    rest_api.register_blueprint(patients.hospital_blp)
    rest_api.register_blueprint(audit.blp)
    app.register_blueprint(health.bp)

    _register_request_hooks(app)
    _register_error_handlers(app)

    from careflow.cli import register_cli

    register_cli(app)
    return app


def _register_request_hooks(app: Flask) -> None:
    @app.before_request
    def _start_request():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        g.request_started = time.perf_counter()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=g.request_id)

    @app.after_request
    def _finish_request(response):
        response.headers["X-Request-ID"] = g.get("request_id", "")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.path.startswith("/api/v1/"):
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if app.config["APP_ENV"] == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        actor = g.get("actor")
        log.info(
            "request",
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - g.get("request_started", time.perf_counter())) * 1000, 1),
            user_id=str(actor.user_id) if actor else None,
        )
        return response


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _app_error(err: AppError):
        return error_response(err.status_code, err.code, err.message, err.details)

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):
        from werkzeug.exceptions import HTTPException

        if isinstance(err, HTTPException):
            from careflow.errors import http_exception_response

            return http_exception_response(err)
        db.session.rollback()
        log.exception("unhandled_error", error_type=type(err).__name__)
        return error_response(500, "internal_error", "An unexpected error occurred.")
