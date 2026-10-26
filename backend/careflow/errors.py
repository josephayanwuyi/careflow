"""Domain errors and the uniform JSON error format.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}
"""

from __future__ import annotations

from typing import Any

from flask import g, jsonify
from werkzeug.exceptions import HTTPException


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    message = "The request could not be processed."

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None):
        super().__init__(message or self.message)
        self.message = message or self.message
        if code:
            self.code = code
        self.details = details


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
    message = "The request contains invalid data."


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Authentication is required."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class TooManyRequests(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please wait and try again."


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "rate_limited",
    500: "internal_error",
}


def error_response(status: int, code: str, message: str, details: Any = None, headers=None):
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": getattr(g, "request_id", None),
        }
    }
    response = jsonify(body)
    response.status_code = status
    if headers:
        response.headers.update(headers)
    return response


def http_exception_response(error: HTTPException):
    """Convert werkzeug/webargs HTTP exceptions into the uniform error format."""
    status = error.code or 500
    data = getattr(error, "data", None) or {}
    details = data.get("errors") or data.get("messages")
    if status == 422:
        message = "The request contains invalid data."
    elif status >= 500:
        message = "An unexpected error occurred."
    else:
        message = data.get("message") or error.description or error.name
    return error_response(status, _HTTP_CODES.get(status, "error"), message, details, data.get("headers"))
