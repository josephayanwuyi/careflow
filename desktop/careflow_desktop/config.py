"""Desktop configuration from environment variables (no secrets are stored)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_url: str
    request_timeout: float
    refresh_interval_s: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_url=os.environ.get("CAREFLOW_API_URL", "http://127.0.0.1:8000").rstrip("/"),
            request_timeout=float(os.environ.get("CAREFLOW_TIMEOUT_SECONDS", "10")),
            # Periodic refresh as a fallback in case live updates are interrupted.
            refresh_interval_s=int(os.environ.get("CAREFLOW_REFRESH_SECONDS", "60")),
        )
