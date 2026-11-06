"""Doctor availability: explicit, event-based statuses.

The doctor's own report (`self_reported`) and a receptionist's confirmation (`staff_verified`)
are recorded separately so staff can tell them apart. Location/GPS is not used.

A status set on an earlier day does not carry over: at the start of each hospital-local day
the effective status is `off_duty` until the doctor checks in again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select

from careflow.errors import Conflict, Forbidden, NotFound, ValidationFailed
from careflow.extensions import db
from careflow.models import (
    DoctorProfile,
    DoctorScheduleException,
    DoctorScheduleTemplate,
    DoctorStatus as D,
    DoctorStatusEvent,
    Queue,
    QueueEntry,
    QueueStatus,
)
from careflow.models.base import utcnow
from careflow.realtime import publish
from careflow.security.policy import Actor, Perm, require
from careflow.services import audit_service
from careflow.services.hospital_service import get_hospital
from careflow.services.hospital_settings import hospital_tz

# Allowed self-reported changes: current effective status -> statuses the doctor may set.
_SELF_TRANSITIONS = {
    D.OFF_DUTY: {D.CHECKED_IN},
    D.CHECKED_OUT: {D.CHECKED_IN},
    D.CHECKED_IN: {D.AVAILABLE, D.ON_BREAK, D.TEMPORARILY_UNAVAILABLE, D.CHECKED_OUT},
    D.AVAILABLE: {D.ON_BREAK, D.TEMPORARILY_UNAVAILABLE, D.CHECKED_OUT},
    D.ON_BREAK: {D.AVAILABLE, D.TEMPORARILY_UNAVAILABLE, D.CHECKED_OUT},
    D.TEMPORARILY_UNAVAILABLE: {D.AVAILABLE, D.ON_BREAK, D.CHECKED_OUT},
}


def local_today(doctor_or_hospital) -> date:
    hospital = getattr(doctor_or_hospital, "hospital", doctor_or_hospital)
    return utcnow().astimezone(hospital_tz(hospital)).date()


def effective_status(doctor: DoctorProfile, now: datetime | None = None) -> str:
    now = now or utcnow()
    if doctor.current_status_at is None:
        return D.OFF_DUTY
    tz = hospital_tz(doctor.hospital)
    if doctor.current_status_at.astimezone(tz).date() < now.astimezone(tz).date():
        return D.OFF_DUTY
    return doctor.current_status


def effective_expected_return(doctor: DoctorProfile, now: datetime | None = None) -> datetime | None:
    if effective_status(doctor, now) in (D.ON_BREAK, D.TEMPORARILY_UNAVAILABLE):
        return doctor.expected_return_at
    return None


def todays_sessions(doctor: DoctorProfile, now: datetime | None = None) -> list[tuple[datetime, datetime]]:
    """Today's scheduled sessions (UTC start/end), from templates and extra sessions, minus whole-day blocks."""
    now = now or utcnow()
    tz = hospital_tz(doctor.hospital)
    today = now.astimezone(tz).date()
    exceptions = db.session.scalars(select(DoctorScheduleException).where(
        DoctorScheduleException.doctor_id == doctor.id, DoctorScheduleException.on_date == today)).all()
    if any(e.kind == "unavailable" and e.start_time is None for e in exceptions):
        return []
    templates = db.session.scalars(select(DoctorScheduleTemplate).where(
        DoctorScheduleTemplate.doctor_id == doctor.id, DoctorScheduleTemplate.is_active.is_(True),
        DoctorScheduleTemplate.weekday == today.weekday(), DoctorScheduleTemplate.valid_from <= today,
        (DoctorScheduleTemplate.valid_until.is_(None)) | (DoctorScheduleTemplate.valid_until >= today))).all()
    sessions = [(t.start_time, t.end_time) for t in templates]
    sessions += [(e.start_time, e.end_time) for e in exceptions if e.kind == "extra_session"]
    return sorted((datetime.combine(today, s, tzinfo=tz).astimezone(UTC),
                   datetime.combine(today, e, tzinfo=tz).astimezone(UTC)) for s, e in sessions)


def next_session_start(doctor: DoctorProfile, now: datetime | None = None) -> datetime | None:
    """Start of today's next session that has not started yet (None if the doctor is late or has none)."""
    now = now or utcnow()
    upcoming = [start for start, _ in todays_sessions(doctor, now) if start > now]
    return upcoming[0] if upcoming else None


def _consultation_in_progress(doctor_id: uuid.UUID) -> bool:
    return bool(db.session.scalar(
        select(func.count()).select_from(QueueEntry).join(Queue, Queue.id == QueueEntry.queue_id)
        .where(Queue.doctor_id == doctor_id, QueueEntry.status == QueueStatus.IN_CONSULTATION)))


def public_status(doctor: DoctorProfile, now: datetime | None = None) -> str:
    """Patient-facing label. Only statuses backed by actual events are shown."""
    status = effective_status(doctor, now)
    if status == D.OFF_DUTY:
        return "expected" if any(end > (now or utcnow()) for _, end in todays_sessions(doctor, now)) else "unavailable"
    if status == D.CHECKED_OUT:
        return "unavailable"
    if status in (D.AVAILABLE, D.CHECKED_IN) and _consultation_in_progress(doctor.id):
        return "consultation_in_progress"
    return {D.CHECKED_IN: "checked_in", D.AVAILABLE: "available", D.ON_BREAK: "on_break",
            D.TEMPORARILY_UNAVAILABLE: "temporarily_unavailable"}[status]


def _set_status(doctor: DoctorProfile, *, status: str, source: str, actor: Actor | None, note: str | None,
                expected_return_at: datetime | None) -> DoctorStatusEvent:
    now = utcnow()
    doctor.current_status = status
    doctor.current_status_source = source
    doctor.current_status_at = now
    doctor.expected_return_at = expected_return_at
    doctor.status_note = note
    event = DoctorStatusEvent(doctor_id=doctor.id, hospital_id=doctor.hospital_id, status=status, source=source,
                              note=note, expected_return_at=expected_return_at,
                              actor_user_id=actor.user_id if actor else None, occurred_at=now)
    db.session.add(event)
    audit_service.record("doctor.status_changed", actor=actor, hospital_id=doctor.hospital_id, target_type="doctor",
                         target_id=doctor.id, details={"status": status, "source": source})
    publish("doctor_status", hospital_id=doctor.hospital_id, doctor_id=doctor.id, status=status)
    return event


def _own_doctor(actor: Actor, hospital_id: uuid.UUID | None, *, lock: bool = True) -> DoctorProfile:
    if not actor.doctor_ids:
        raise Forbidden("Only doctors can change their availability.")
    if hospital_id is None:
        if len(actor.doctor_ids) > 1:
            raise ValidationFailed(details={"hospital_id": ["Required when you work at more than one hospital."]})
        hospital_id = next(iter(actor.doctor_ids))
    doctor_id = actor.doctor_ids.get(hospital_id)
    if doctor_id is None:
        raise Forbidden("You are not a doctor at this hospital.")
    require(actor, Perm.AVAILABILITY_SELF, hospital_id)
    if not lock:
        return db.session.get(DoctorProfile, doctor_id)
    return db.session.scalar(select(DoctorProfile).where(DoctorProfile.id == doctor_id).with_for_update()
                             .execution_options(populate_existing=True))


def get_own(actor: Actor, hospital_id: uuid.UUID | None) -> DoctorProfile:
    return _own_doctor(actor, hospital_id, lock=False)


def set_own_status(actor: Actor, *, hospital_id: uuid.UUID | None, status: str, note: str | None,
                   expected_return_at: datetime | None) -> DoctorProfile:
    doctor = _own_doctor(actor, hospital_id)
    now = utcnow()
    current = effective_status(doctor, now)
    if status == current:
        raise Conflict(f"You are already {status.replace('_', ' ')}.", code="status_unchanged")
    if status not in _SELF_TRANSITIONS.get(current, set()):
        hint = " Check in first." if current in (D.OFF_DUTY, D.CHECKED_OUT) else ""
        raise Conflict(f"You cannot change from {current.replace('_', ' ')} to {status.replace('_', ' ')}.{hint}",
                       code="invalid_status_change", details={"current_status": current})
    if expected_return_at is not None:
        if status not in (D.ON_BREAK, D.TEMPORARILY_UNAVAILABLE):
            raise ValidationFailed(details={"expected_return_at": ["Only for breaks or temporary unavailability."]})
        if not (now < expected_return_at <= now + timedelta(hours=12)):
            raise ValidationFailed(details={"expected_return_at": ["Must be in the next 12 hours."]})
    if status == D.CHECKED_OUT and _consultation_in_progress(doctor.id):
        raise Conflict("Complete the current consultation before checking out.", code="consultation_in_progress")
    _set_status(doctor, status=status, source="self_reported", actor=actor, note=note,
                expected_return_at=expected_return_at)
    db.session.commit()
    return doctor


def verify_arrival(actor: Actor, hospital_id: uuid.UUID, doctor_id: uuid.UUID, note: str | None) -> DoctorProfile:
    """Receptionist confirms the doctor is physically present."""
    get_hospital(hospital_id)
    require(actor, Perm.AVAILABILITY_VERIFY, hospital_id)
    doctor = db.session.scalar(select(DoctorProfile).where(DoctorProfile.id == doctor_id).with_for_update()
                               .execution_options(populate_existing=True))
    if doctor is None or doctor.hospital_id != hospital_id:
        raise NotFound("Doctor not found.")
    now = utcnow()
    doctor.arrival_verified_at = now
    doctor.arrival_verified_by_user_id = actor.user_id
    current = effective_status(doctor, now)
    status = D.CHECKED_IN if current in (D.OFF_DUTY, D.CHECKED_OUT) else current
    _set_status(doctor, status=status, source="staff_verified", actor=actor, note=note,
                expected_return_at=effective_expected_return(doctor, now))
    db.session.commit()
    return doctor


def arrival_verified_today(doctor: DoctorProfile, now: datetime | None = None) -> bool:
    if doctor.arrival_verified_at is None:
        return False
    tz = hospital_tz(doctor.hospital)
    return doctor.arrival_verified_at.astimezone(tz).date() == (now or utcnow()).astimezone(tz).date()


def list_for_hospital(actor: Actor, hospital_id: uuid.UUID) -> list[DoctorProfile]:
    get_hospital(hospital_id)
    if not actor.roles_in(hospital_id):
        raise Forbidden()
    return list(db.session.scalars(select(DoctorProfile).where(
        DoctorProfile.hospital_id == hospital_id, DoctorProfile.is_active.is_(True))))


def history(actor: Actor, doctor_id: uuid.UUID, *, limit: int = 100) -> list[DoctorStatusEvent]:
    doctor = db.session.get(DoctorProfile, doctor_id)
    if doctor is None or not (actor.roles_in(doctor.hospital_id) - {"doctor"} or actor.is_doctor(doctor_id)):
        raise NotFound("Doctor not found.")
    return list(db.session.scalars(select(DoctorStatusEvent).where(DoctorStatusEvent.doctor_id == doctor_id)
                                   .order_by(DoctorStatusEvent.occurred_at.desc()).limit(limit)))


def today_window(hospital) -> tuple[datetime, datetime]:
    tz = hospital_tz(hospital)
    today = utcnow().astimezone(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)
