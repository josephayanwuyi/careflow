"""Structured JSON logging with a scrubber for sensitive fields."""

from __future__ import annotations

import logging
import sys

import structlog

# Keys whose values must never reach the logs.
_SENSITIVE_KEYS = {
    "password", "current_password", "new_password", "temporary_password", "token", "access_token",
    "refresh_token", "authorization", "cookie", "full_name", "phone", "email", "date_of_birth",
}


def _scrub(_, __, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _scrub,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
