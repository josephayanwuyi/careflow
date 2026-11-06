from __future__ import annotations


class ApiError(Exception):
    """An error reported by the CareFlow API, or a failure to reach it."""

    def __init__(self, status: int, code: str, message: str, details=None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.request_id = request_id

    @property
    def user_message(self) -> str:
        """Message suitable for showing to staff, including field-level validation problems."""
        text = self.message
        fields = _flatten_details(self.details)
        if fields:
            text += "\n\n" + "\n".join(f"• {name}: {problem}" for name, problem in fields)
        if self.request_id and self.status >= 500:
            text += f"\n\nReference: {self.request_id}"
        return text

    def __repr__(self) -> str:
        return f"ApiError({self.status}, {self.code!r}, {self.message!r})"


class NetworkError(ApiError):
    """The server could not be reached or did not answer in time."""

    def __init__(self, message: str):
        super().__init__(0, "network_error", message)


class SessionExpired(ApiError):
    """The session ended (expired, revoked, or logged out elsewhere); the user must sign in again."""

    def __init__(self, message: str = "Your session has ended. Please sign in again."):
        super().__init__(401, "session_ended", message)


def _flatten_details(details, prefix: str = "") -> list[tuple[str, str]]:
    """Turn {"json": {"reason": ["Required"]}} into [("reason", "Required")]."""
    out: list[tuple[str, str]] = []
    if isinstance(details, dict):
        for key, value in details.items():
            if key in ("json", "query", "form", "headers") and isinstance(value, dict):
                out += _flatten_details(value, prefix)
            elif key == "candidates":
                continue
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                out += [(f"{prefix}{key}".replace("_", " "), v) for v in value]
            elif isinstance(value, (dict, list)):
                out += _flatten_details(value, f"{prefix}{key}.")
    return out
