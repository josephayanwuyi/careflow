"""Waiting-time estimation. Pure functions, no database access.

Model (deliberately simple and explainable):

    estimate = time until the doctor can see patients
             + remaining time of the patient currently with the doctor
             + patients_ahead x typical consultation length

The typical length comes from this doctor's recent completed consultations:
- with at least `min_samples` durations: median, with the 25th-75th percentile as the range;
- otherwise: the configured fallback (doctor default), range 0.75x-1.5x, flagged *provisional*.

The result is always a range rounded to 5 minutes. When the doctor is checked out, or
unavailable/paused with no expected return, no number is given: we don't pretend to know.
Limitations are documented in docs/wait-time-estimation.md.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime

from careflow.models.availability import DoctorStatus as D


@dataclass(frozen=True)
class DurationStats:
    typical: float
    low: float
    high: float
    sample_size: int
    provisional: bool


def duration_stats(durations_minutes: list[float], *, fallback_minutes: float, min_samples: int) -> DurationStats:
    data = sorted(d for d in durations_minutes if d > 0)
    if len(data) >= max(min_samples, 4):
        q1, median, q3 = statistics.quantiles(data, n=4)
        # Drop only clear outliers (e.g. a consultation left open): beyond both 3 IQRs and twice the median.
        upper_fence = max(q3 + 3 * (q3 - q1), 2 * median)
        trimmed = [d for d in data if d <= upper_fence] or data
        q1, median, q3 = statistics.quantiles(trimmed, n=4)
        return DurationStats(typical=statistics.median(trimmed), low=max(q1, 1.0), high=max(q3, q1 + 1),
                             sample_size=len(trimmed), provisional=False)
    return DurationStats(typical=fallback_minutes, low=fallback_minutes * 0.75, high=fallback_minutes * 1.5,
                         sample_size=len(data), provisional=True)


@dataclass(frozen=True)
class EstimateInputs:
    now: datetime
    entry_status: str                       # the entry being estimated
    patients_ahead: int                     # WAITING entries ordered before this one
    doctor_status: str                      # effective status (see availability_service.effective_status)
    expected_return_at: datetime | None
    next_session_start: datetime | None     # today's next scheduled session start, if any
    queue_paused: bool
    with_doctor_status: str | None          # status of the entry currently with the doctor (called/in_consultation)
    with_doctor_started_at: datetime | None
    stats: DurationStats


@dataclass
class Estimate:
    state: str                    # estimated | next | called | being_seen | unavailable | not_waiting
    min_minutes: int | None = None
    max_minutes: int | None = None
    provisional: bool = False
    explanation: str = ""
    basis: dict = field(default_factory=dict)


def _floor5(x: float) -> int:
    return max(0, int(math.floor(x / 5.0)) * 5)


def _ceil5(x: float) -> int:
    return int(math.ceil(x / 5.0)) * 5


def estimate(inp: EstimateInputs) -> Estimate:
    if inp.entry_status == "called":
        return Estimate("called", 0, 0, explanation="You have been called. Please go to the consultation room.")
    if inp.entry_status == "in_consultation":
        return Estimate("being_seen", 0, 0, explanation="Your consultation is in progress.")
    if inp.entry_status != "waiting":
        return Estimate("not_waiting", explanation="You are not currently waiting in this queue.")

    s = inp.stats
    basis = {"patients_ahead": inp.patients_ahead, "typical_consultation_minutes": round(s.typical, 1),
             "sample_size": s.sample_size}

    # 1. When can the doctor start seeing patients?
    if inp.doctor_status == D.CHECKED_OUT:
        return Estimate("unavailable", basis=basis, explanation="The doctor has finished for the day.")
    offset = 0.0
    if inp.queue_paused or inp.doctor_status in (D.ON_BREAK, D.TEMPORARILY_UNAVAILABLE):
        if inp.expected_return_at is None:
            return Estimate("unavailable", basis=basis,
                            explanation="The doctor is temporarily unavailable and no return time is known yet.")
        offset = max(0.0, (inp.expected_return_at - inp.now).total_seconds() / 60)
    elif inp.doctor_status == D.OFF_DUTY:
        if inp.next_session_start is None:
            return Estimate("unavailable", basis=basis, explanation="The doctor has not arrived yet.")
        offset = max(0.0, (inp.next_session_start - inp.now).total_seconds() / 60)
    basis["minutes_until_doctor_available"] = round(offset)

    # 2. Remaining time for whoever is with the doctor now.
    current_low = current_high = 0.0
    if inp.with_doctor_status == "in_consultation" and inp.with_doctor_started_at:
        elapsed = (inp.now - inp.with_doctor_started_at).total_seconds() / 60
        current_low, current_high = max(s.low - elapsed, 1.0), max(s.high - elapsed, 2.0)
    elif inp.with_doctor_status == "called":
        current_low, current_high = s.low, s.high

    # 3. Patients ahead.
    low = offset + current_low + inp.patients_ahead * s.low
    high = offset + current_high + inp.patients_ahead * s.high
    lo, hi = _floor5(low), _ceil5(high)
    if hi < lo + 5:
        hi = lo + 5

    state = "next" if inp.patients_ahead == 0 and offset == 0 and inp.with_doctor_status is None else "estimated"
    if state == "next":
        explanation = "You are next."
    else:
        explanation = f"{inp.patients_ahead} patient(s) ahead of you."
    if s.provisional:
        explanation += " This estimate is provisional: there is not yet enough history for this doctor."
    return Estimate(state, lo, hi, provisional=s.provisional, explanation=explanation, basis=basis)
