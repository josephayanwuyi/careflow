"""Display helpers: local times, human-readable statuses, colours."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

STATUS_LABELS = {
    # Appointments
    "requested": "Pending approval",
    "approved": "Approved",
    "rejected": "Rejected",
    "reschedule_proposed": "New time proposed",
    "cancelled": "Cancelled",
    "expired": "Expired",
    "checked_in": "Checked in",
    "completed": "Completed",
    "no_show": "No-show",
    # Queue
    "waiting": "Waiting",
    "called": "Called",
    "in_consultation": "In consultation",
    "missed": "Missed call",
    "transferred": "Transferred",
    # Doctors
    "off_duty": "Off duty",
    "available": "Available",
    "on_break": "On break",
    "temporarily_unavailable": "Temporarily unavailable",
    "checked_out": "Checked out",
    "expected": "Expected",
    "consultation_in_progress": "Consultation in progress",
    "unavailable": "Unavailable",
}

# (background, text) — chosen for contrast; status is always also shown as text.
STATUS_COLORS = {
    "requested": ("#FFF4CE", "#7A5A00"),
    "reschedule_proposed": ("#FFF4CE", "#7A5A00"),
    "approved": ("#DFF6DD", "#1E5E1E"),
    "checked_in": ("#DCEBFF", "#0B3D91"),
    "completed": ("#E8E8E8", "#333333"),
    "rejected": ("#FDE7E9", "#8A1F11"),
    "cancelled": ("#E8E8E8", "#555555"),
    "expired": ("#E8E8E8", "#555555"),
    "no_show": ("#FDE7E9", "#8A1F11"),
    "waiting": ("#DCEBFF", "#0B3D91"),
    "called": ("#FFE8CC", "#8A4B00"),
    "in_consultation": ("#DFF6DD", "#1E5E1E"),
    "missed": ("#FDE7E9", "#8A1F11"),
    "transferred": ("#E8E8E8", "#555555"),
    "available": ("#DFF6DD", "#1E5E1E"),
    "checked_in_doctor": ("#DCEBFF", "#0B3D91"),
    "consultation_in_progress": ("#DFF6DD", "#1E5E1E"),
    "on_break": ("#FFF4CE", "#7A5A00"),
    "temporarily_unavailable": ("#FFE8CC", "#8A4B00"),
    "expected": ("#E8E8E8", "#333333"),
    "off_duty": ("#E8E8E8", "#555555"),
    "checked_out": ("#E8E8E8", "#555555"),
    "unavailable": ("#E8E8E8", "#555555"),
}


def status_label(status: str | None) -> str:
    if not status:
        return ""
    return STATUS_LABELS.get(status, status.replace("_", " ").capitalize())


def parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def local(value: str | None, tz: str, fmt: str = "%a %d %b, %H:%M") -> str:
    dt = parse_iso(value)
    return dt.astimezone(ZoneInfo(tz)).strftime(fmt) if dt else ""


def local_time(value: str | None, tz: str) -> str:
    return local(value, tz, "%H:%M")


def wait_text(estimate: dict | None) -> str:
    if not estimate:
        return ""
    state = estimate.get("state")
    if state == "next":
        return "Next"
    if state == "called":
        return "Called"
    if state == "being_seen":
        return "With doctor"
    if state == "estimated":
        text = f"{estimate['min_minutes']}–{estimate['max_minutes']} min"
        return text + " (provisional)" if estimate.get("provisional") else text
    if state == "unavailable":
        return "Not available"
    return ""


EVENT_LABELS = {
    "checked_in": "Checked in",
    "walk_in_added": "Walk-in added",
    "called": "Called",
    "recalled": "Called again",
    "consultation_started": "Consultation started",
    "consultation_completed": "Consultation completed",
    "missed": "Missed call",
    "returned": "Returned to queue",
    "cancelled": "Left the queue",
    "transferred_out": "Transferred out",
    "transferred_in": "Transferred in",
    "priority_changed": "Priority changed",
    "arrival_confirmed": "Arrival confirmed",
    "paused": "Queue paused",
    "resumed": "Queue resumed",
    "closed_end_of_day": "Closed at end of day",
}


def event_label(event_type: str) -> str:
    return EVENT_LABELS.get(event_type, event_type.replace("_", " ").replace(".", ": ").capitalize())
