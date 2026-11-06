"""Unit tests for the appointment state machine (no database)."""

import pytest

from careflow.errors import Conflict, ValidationFailed
from careflow.models.appointment import AppointmentStatus as S
from careflow.services.appointment_rules import TRANSITIONS, allowed_events, check_transition


@pytest.mark.parametrize("event, start, end, actor", [
    ("approve", S.REQUESTED, S.APPROVED, "staff"),
    ("propose_reschedule", S.REQUESTED, S.RESCHEDULE_PROPOSED, "staff"),
    ("propose_reschedule", S.APPROVED, S.RESCHEDULE_PROPOSED, "staff"),
    ("accept_reschedule", S.RESCHEDULE_PROPOSED, S.APPROVED, "patient"),
    ("decline_reschedule", S.RESCHEDULE_PROPOSED, S.CANCELLED, "patient"),
    ("cancel_by_patient", S.APPROVED, S.CANCELLED, "patient"),
    ("expire", S.REQUESTED, S.EXPIRED, "system"),
    ("mark_no_show", S.APPROVED, S.NO_SHOW, "staff"),
    ("check_in", S.APPROVED, S.CHECKED_IN, "patient"),
    ("complete", S.CHECKED_IN, S.COMPLETED, "system"),
])
def test_valid_transitions(event, start, end, actor):
    reason = "some reason" if TRANSITIONS[event].reason_required else None
    assert check_transition(event, start, actor, reason) == end


@pytest.mark.parametrize("event, start", [
    ("approve", S.APPROVED),
    ("approve", S.REJECTED),
    ("approve", S.CANCELLED),
    ("reject", S.APPROVED),
    ("accept_reschedule", S.APPROVED),
    ("cancel_by_patient", S.CHECKED_IN),
    ("cancel_by_patient", S.COMPLETED),
    ("mark_no_show", S.REQUESTED),
    ("complete", S.APPROVED),
])
def test_invalid_transitions_raise_conflict(event, start):
    actor = next(iter(TRANSITIONS[event].actors))
    with pytest.raises(Conflict) as err:
        check_transition(event, start, actor, "reason text")
    assert err.value.code == "invalid_transition"


def test_terminal_states_have_no_exits():
    for status in S.TERMINAL:
        for actor in ("patient", "staff", "system"):
            assert allowed_events(status, actor) == []


@pytest.mark.parametrize("event", ["reject", "propose_reschedule", "cancel_by_staff"])
def test_reason_required(event):
    start = next(iter(TRANSITIONS[event].from_statuses))
    with pytest.raises(ValidationFailed):
        check_transition(event, start, "staff", "   ")


def test_wrong_actor_is_a_programming_error():
    with pytest.raises(ValueError):
        check_transition("approve", S.REQUESTED, "patient")


def test_submission_is_not_approval():
    # A patient can never move a request to approved.
    assert "approve" not in allowed_events(S.REQUESTED, "patient")
    assert allowed_events(S.REQUESTED, "patient") == ["cancel_by_patient"]
