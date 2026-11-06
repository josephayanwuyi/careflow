"""The appointment state machine. Pure functions, no database access.

Each event names the statuses it may start from, the status it leads to, and who may
trigger it. Services must call `check_transition()` before changing an appointment's status.
"""

from __future__ import annotations

from dataclasses import dataclass

from careflow.errors import Conflict, ValidationFailed
from careflow.models.appointment import AppointmentStatus as S


@dataclass(frozen=True)
class Transition:
    from_statuses: frozenset[str]
    to_status: str
    actors: frozenset[str]          # "patient", "staff", "system"
    reason_required: bool = False


TRANSITIONS: dict[str, Transition] = {
    # Staff review of a patient's request
    "approve": Transition(frozenset({S.REQUESTED}), S.APPROVED, frozenset({"staff"})),
    "reject": Transition(frozenset({S.REQUESTED}), S.REJECTED, frozenset({"staff"}), reason_required=True),
    "propose_reschedule": Transition(frozenset({S.REQUESTED, S.APPROVED}), S.RESCHEDULE_PROPOSED,
                                     frozenset({"staff"}), reason_required=True),
    # Patient response to a proposal. Acceptance is recorded as a "rescheduled" event.
    "accept_reschedule": Transition(frozenset({S.RESCHEDULE_PROPOSED}), S.APPROVED, frozenset({"patient"})),
    "decline_reschedule": Transition(frozenset({S.RESCHEDULE_PROPOSED}), S.CANCELLED, frozenset({"patient"})),
    # Cancellation
    "cancel_by_patient": Transition(frozenset({S.REQUESTED, S.APPROVED, S.RESCHEDULE_PROPOSED}), S.CANCELLED,
                                    frozenset({"patient"})),
    "cancel_by_staff": Transition(frozenset({S.REQUESTED, S.APPROVED, S.RESCHEDULE_PROPOSED}), S.CANCELLED,
                                  frozenset({"staff"}), reason_required=True),
    # Time-based
    "expire": Transition(frozenset({S.REQUESTED, S.RESCHEDULE_PROPOSED}), S.EXPIRED, frozenset({"system"})),
    "mark_no_show": Transition(frozenset({S.APPROVED}), S.NO_SHOW, frozenset({"staff", "system"})),
    # Queue phase (Phase 4) drives these
    "check_in": Transition(frozenset({S.APPROVED}), S.CHECKED_IN, frozenset({"patient", "staff"})),
    "complete": Transition(frozenset({S.CHECKED_IN}), S.COMPLETED, frozenset({"staff", "system"})),
    # Leaving the queue after check-in
    "cancel_checked_in": Transition(frozenset({S.CHECKED_IN}), S.CANCELLED, frozenset({"patient", "staff"})),
    "close_unseen": Transition(frozenset({S.CHECKED_IN}), S.NO_SHOW, frozenset({"system"})),
}


def check_transition(event: str, current_status: str, actor_kind: str, reason: str | None = None) -> str:
    """Return the target status, or raise Conflict if the transition is not allowed."""
    rule = TRANSITIONS.get(event)
    if rule is None:
        raise ValueError(f"Unknown appointment event {event!r}")
    if actor_kind not in rule.actors:
        raise ValueError(f"{actor_kind!r} cannot trigger {event!r}")  # programming error, not user error
    if current_status not in rule.from_statuses:
        raise Conflict(
            f"This appointment is {current_status.replace('_', ' ')} and cannot be changed this way.",
            code="invalid_transition",
            details={"event": event, "current_status": current_status},
        )
    if rule.reason_required and not (reason and reason.strip()):
        raise ValidationFailed(details={"reason": ["A reason is required."]})
    return rule.to_status


def allowed_events(current_status: str, actor_kind: str) -> list[str]:
    """Events an actor kind could trigger from this status (used to drive UI buttons)."""
    return sorted(e for e, t in TRANSITIONS.items() if current_status in t.from_statuses and actor_kind in t.actors)
