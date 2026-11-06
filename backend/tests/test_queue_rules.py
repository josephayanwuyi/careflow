"""Unit tests for queue ordering and transitions (no database)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from careflow.errors import Conflict
from careflow.services.queue_rules import allowed_events, check_transition, initial_sort_key, ordered_waiting

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
GRACE = timedelta(minutes=15)


@dataclass
class E:
    name: str
    sort_key: datetime
    priority_level: int = 0
    status: str = "waiting"
    entered_queue_at: datetime = T0
    ticket_number: int = 0


def test_punctual_appointment_keeps_booked_time():
    assert initial_sort_key(kind="appointment", checked_in_at=T0 - timedelta(minutes=40),
                            appointment_start=T0, grace=GRACE) == T0
    assert initial_sort_key(kind="appointment", checked_in_at=T0 + timedelta(minutes=10),
                            appointment_start=T0, grace=GRACE) == T0


def test_late_appointment_uses_check_in_time():
    late = T0 + timedelta(minutes=20)
    assert initial_sort_key(kind="appointment", checked_in_at=late, appointment_start=T0, grace=GRACE) == late


def test_walk_in_uses_check_in_time():
    assert initial_sort_key(kind="walk_in", checked_in_at=T0, appointment_start=None, grace=GRACE) == T0


def test_ordering_priority_then_sort_key_then_ticket():
    entries = [
        E("late", T0 + timedelta(minutes=30), ticket_number=1),
        E("punctual", T0, ticket_number=2),
        E("urgent", T0 + timedelta(minutes=50), priority_level=2, ticket_number=3),
        E("tie_b", T0 + timedelta(minutes=10), ticket_number=5),
        E("tie_a", T0 + timedelta(minutes=10), ticket_number=4),
        E("called", T0 - timedelta(hours=1), status="called", ticket_number=6),
    ]
    assert [e.name for e in ordered_waiting(entries)] == ["urgent", "punctual", "tie_a", "tie_b", "late"]


@pytest.mark.parametrize("event, start, end", [
    ("call", "waiting", "called"),
    ("start", "called", "in_consultation"),
    ("complete", "in_consultation", "completed"),
    ("mark_missed", "called", "missed"),
    ("return", "missed", "waiting"),
    ("transfer", "waiting", "transferred"),
    ("cancel", "missed", "cancelled"),
])
def test_valid_transitions(event, start, end):
    assert check_transition(event, start, "staff") == end


@pytest.mark.parametrize("event, start", [
    ("call", "called"), ("start", "waiting"), ("complete", "called"), ("return", "waiting"),
    ("cancel", "in_consultation"), ("cancel", "completed"), ("transfer", "in_consultation"),
])
def test_invalid_transitions(event, start):
    with pytest.raises(Conflict):
        check_transition(event, start, "staff")


def test_patients_cannot_drive_the_queue():
    assert allowed_events("waiting", "patient") == ["cancel"]
    with pytest.raises(ValueError):
        check_transition("call", "waiting", "patient")


def test_doctor_cannot_return_or_transfer():
    assert "return" not in allowed_events("missed", "doctor")
    assert "transfer" not in allowed_events("waiting", "doctor")
