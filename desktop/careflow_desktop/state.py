"""Shared state for the signed-in staff member: user, selected hospital, permissions, live events."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from careflow_desktop.api.client import ApiClient

DESK_ROLES = {"receptionist", "hospital_admin"}


@dataclass
class Hospital:
    id: str
    name: str
    roles: list[str]
    permissions: set[str] = field(default_factory=set)
    timezone: str = "Africa/Lagos"


def desk_hospitals(user: dict) -> list[Hospital]:
    """Hospitals where the user may use the desktop app (receptionist or hospital admin)."""
    return [Hospital(m["hospital_id"], m["hospital_name"], m["roles"], set(m["permissions"]))
            for m in user.get("memberships", []) if DESK_ROLES & set(m["roles"])]


class AppState(QObject):
    hospital_changed = Signal()
    server_event = Signal(dict)       # re-emitted live update events
    stream_state = Signal(str)        # connecting | live | offline
    session_expired = Signal()
    status_message = Signal(str)      # short confirmations for the status bar

    def __init__(self, client: ApiClient):
        super().__init__()
        self.client = client
        self.user: dict | None = None
        self.hospitals: list[Hospital] = []
        self.hospital: Hospital | None = None
        self.doctors: list[dict] = []   # cached directory for the selected hospital

    def can(self, permission: str) -> bool:
        return self.hospital is not None and permission in self.hospital.permissions

    @property
    def hospital_id(self) -> str:
        assert self.hospital is not None
        return self.hospital.id

    def doctor_name(self, doctor_id: str) -> str:
        return next((d["display_name"] for d in self.doctors if d["id"] == doctor_id), "Unknown doctor")
