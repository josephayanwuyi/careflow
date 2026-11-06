"""Queue state machine and ordering rules. Pure functions, no database access.

Ordering (documented rule, applied only to WAITING entries):

1. Higher `priority_level` first. Priority is set only by authorised staff with a reason;
   the software never assigns it.
2. Then earlier `sort_key`:
   - appointment patients who check in no later than `start + grace` keep their booked
     start time as sort key (punctual patients are seen in schedule order);
   - late appointment patients and walk-ins use their check-in time;
   - a patient returning after a missed call gets the return time (back of the queue).
3. Ties: earlier `entered_queue_at`, then lower ticket number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from careflow.errors import Conflict
from careflow.models.queue import QueueStatus as Q


@dataclass(frozen=True)
class Transition:
    from_statuses: frozenset[str]
    to_status: str
    actors: frozenset[str]  # doctor | staff | patient | system


TRANSITIONS: dict[str, Transition] = {
    "call": Transition(frozenset({Q.WAITING}), Q.CALLED, frozenset({"doctor", "staff"})),
    "recall": Transition(frozenset({Q.CALLED}), Q.CALLED, frozenset({"doctor", "staff"})),
    "start": Transition(frozenset({Q.CALLED}), Q.IN_CONSULTATION, frozenset({"doctor", "staff"})),
    "complete": Transition(frozenset({Q.IN_CONSULTATION}), Q.COMPLETED, frozenset({"doctor", "staff"})),
    "mark_missed": Transition(frozenset({Q.CALLED}), Q.MISSED, frozenset({"doctor", "staff"})),
    "return": Transition(frozenset({Q.MISSED}), Q.WAITING, frozenset({"staff"})),
    "cancel": Transition(frozenset({Q.WAITING, Q.CALLED, Q.MISSED}), Q.CANCELLED, frozenset({"staff", "patient"})),
    "transfer": Transition(frozenset({Q.WAITING, Q.MISSED}), Q.TRANSFERRED, frozenset({"staff"})),
    "set_priority": Transition(frozenset({Q.WAITING}), Q.WAITING, frozenset({"staff"})),
    "confirm_arrival": Transition(frozenset({Q.WAITING, Q.CALLED, Q.MISSED}), "", frozenset({"staff"})),
    "close_day_unseen": Transition(frozenset({Q.WAITING, Q.CALLED, Q.MISSED}), Q.CANCELLED, frozenset({"system"})),
    "close_day_in_consultation": Transition(frozenset({Q.IN_CONSULTATION}), Q.COMPLETED, frozenset({"system"})),
}


def check_transition(event: str, current_status: str, actor_kind: str) -> str:
    """Return the target status (unchanged for in-place events), or raise Conflict."""
    rule = TRANSITIONS[event]
    if actor_kind not in rule.actors:
        raise ValueError(f"{actor_kind!r} cannot trigger {event!r}")
    if current_status not in rule.from_statuses:
        raise Conflict(
            f"This patient is {current_status.replace('_', ' ')}; that action is not possible now.",
            code="invalid_transition", details={"event": event, "current_status": current_status},
        )
    return rule.to_status or current_status


def allowed_events(current_status: str, actor_kind: str) -> list[str]:
    return sorted(e for e, t in TRANSITIONS.items()
                  if current_status in t.from_statuses and actor_kind in t.actors and not e.startswith("close_day"))


def initial_sort_key(*, kind: str, checked_in_at: datetime, appointment_start: datetime | None,
                     grace: timedelta) -> datetime:
    if kind == "appointment" and appointment_start is not None and checked_in_at <= appointment_start + grace:
        return appointment_start
    return checked_in_at


def order_key(entry) -> tuple:
    """Sort key for WAITING entries; works on any object with the queue entry attributes."""
    return (-entry.priority_level, entry.sort_key, entry.entered_queue_at, entry.ticket_number)


def ordered_waiting(entries) -> list:
    return sorted((e for e in entries if e.status == Q.WAITING), key=order_key)
