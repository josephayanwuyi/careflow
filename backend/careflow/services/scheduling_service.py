"""Doctor schedules and bookable slot generation.

Schedules are defined in the hospital's local time. Slots are generated on demand for a
local date and converted to UTC; nothing is pre-materialised. A slot is "available" when no
held appointment (approved, checked in, or a pending staff proposal) overlaps it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, func, or_, select

from careflow.errors import Conflict, NotFound, ValidationFailed
from careflow.extensions import db
from careflow.models import (
    Appointment,
    AppointmentStatus,
    DoctorProfile,
    DoctorScheduleException,
    DoctorScheduleTemplate,
)
from careflow.models.base import utcnow
from careflow.security.policy import Actor, Perm, require
from careflow.services import audit_service
from careflow.services.hospital_service import get_hospital
from careflow.services.hospital_settings import get_setting, hospital_tz


@dataclass(frozen=True)
class Slot:
    start_at: datetime
    end_at: datetime


# --- Schedule management -------------------------------------------------------------


def _doctor_in_hospital(hospital_id: uuid.UUID, doctor_id: uuid.UUID) -> DoctorProfile:
    doctor = db.session.get(DoctorProfile, doctor_id)
    if doctor is None or doctor.hospital_id != hospital_id:
        raise NotFound("Doctor not found.")
    return doctor


def _require_staff_view(actor: Actor, hospital_id: uuid.UUID) -> None:
    if not actor.roles_in(hospital_id):
        require(actor, Perm.SCHEDULES_MANAGE, hospital_id)  # raises Forbidden


def list_templates(actor: Actor, hospital_id: uuid.UUID, doctor_id: uuid.UUID) -> list[DoctorScheduleTemplate]:
    get_hospital(hospital_id)
    _require_staff_view(actor, hospital_id)
    _doctor_in_hospital(hospital_id, doctor_id)
    return list(db.session.scalars(
        select(DoctorScheduleTemplate)
        .where(DoctorScheduleTemplate.doctor_id == doctor_id, DoctorScheduleTemplate.is_active.is_(True))
        .order_by(DoctorScheduleTemplate.weekday, DoctorScheduleTemplate.start_time)
    ))


def create_template(actor: Actor, hospital_id: uuid.UUID, doctor_id: uuid.UUID, *, weekday: int, start_time: time,
                    end_time: time, slot_minutes: int, valid_from: date, valid_until: date | None) -> DoctorScheduleTemplate:
    get_hospital(hospital_id)
    require(actor, Perm.SCHEDULES_MANAGE, hospital_id)
    _doctor_in_hospital(hospital_id, doctor_id)
    if start_time >= end_time:
        raise ValidationFailed(details={"end_time": ["Must be after start_time."]})
    if valid_until and valid_until < valid_from:
        raise ValidationFailed(details={"valid_until": ["Must not be before valid_from."]})

    # Reject sessions that overlap an existing active session on the same weekday and dates.
    far_future = date(9999, 12, 31)
    overlapping = db.session.scalar(select(DoctorScheduleTemplate.id).where(
        DoctorScheduleTemplate.doctor_id == doctor_id,
        DoctorScheduleTemplate.is_active.is_(True),
        DoctorScheduleTemplate.weekday == weekday,
        DoctorScheduleTemplate.start_time < end_time,
        DoctorScheduleTemplate.end_time > start_time,
        DoctorScheduleTemplate.valid_from <= (valid_until or far_future),
        func.coalesce(DoctorScheduleTemplate.valid_until, far_future) >= valid_from,
    ))
    if overlapping:
        raise Conflict("This session overlaps an existing session for the doctor.", code="schedule_overlap")

    template = DoctorScheduleTemplate(hospital_id=hospital_id, doctor_id=doctor_id, weekday=weekday,
                                      start_time=start_time, end_time=end_time, slot_minutes=slot_minutes,
                                      valid_from=valid_from, valid_until=valid_until)
    db.session.add(template)
    db.session.flush()
    audit_service.record("schedule.template_created", actor=actor, hospital_id=hospital_id,
                         target_type="schedule_template", target_id=template.id, details={"doctor_id": str(doctor_id)})
    db.session.commit()
    return template


def deactivate_template(actor: Actor, hospital_id: uuid.UUID, template_id: uuid.UUID) -> None:
    require(actor, Perm.SCHEDULES_MANAGE, hospital_id)
    template = db.session.get(DoctorScheduleTemplate, template_id)
    if template is None or template.hospital_id != hospital_id or not template.is_active:
        raise NotFound("Schedule session not found.")
    template.is_active = False
    audit_service.record("schedule.template_deactivated", actor=actor, hospital_id=hospital_id,
                         target_type="schedule_template", target_id=template.id)
    db.session.commit()


def list_exceptions(actor: Actor, hospital_id: uuid.UUID, doctor_id: uuid.UUID,
                    date_from: date | None = None) -> list[DoctorScheduleException]:
    get_hospital(hospital_id)
    _require_staff_view(actor, hospital_id)
    _doctor_in_hospital(hospital_id, doctor_id)
    stmt = select(DoctorScheduleException).where(DoctorScheduleException.doctor_id == doctor_id)
    if date_from:
        stmt = stmt.where(DoctorScheduleException.on_date >= date_from)
    return list(db.session.scalars(stmt.order_by(DoctorScheduleException.on_date, DoctorScheduleException.start_time)))


def create_exception(actor: Actor, hospital_id: uuid.UUID, doctor_id: uuid.UUID, *, on_date: date, kind: str,
                     start_time: time | None, end_time: time | None, slot_minutes: int | None,
                     reason: str | None) -> DoctorScheduleException:
    get_hospital(hospital_id)
    require(actor, Perm.SCHEDULES_MANAGE, hospital_id)
    _doctor_in_hospital(hospital_id, doctor_id)
    if (start_time is None) != (end_time is None):
        raise ValidationFailed(details={"end_time": ["Provide both start_time and end_time, or neither."]})
    if start_time and start_time >= end_time:
        raise ValidationFailed(details={"end_time": ["Must be after start_time."]})
    if kind == "extra_session" and (start_time is None or not slot_minutes):
        raise ValidationFailed(details={"slot_minutes": ["Extra sessions need start_time, end_time and slot_minutes."]})
    exc = DoctorScheduleException(hospital_id=hospital_id, doctor_id=doctor_id, on_date=on_date, kind=kind,
                                  start_time=start_time, end_time=end_time,
                                  slot_minutes=slot_minutes if kind == "extra_session" else None,
                                  reason=reason, created_by_user_id=actor.user_id)
    db.session.add(exc)
    db.session.flush()
    audit_service.record("schedule.exception_created", actor=actor, hospital_id=hospital_id,
                         target_type="schedule_exception", target_id=exc.id,
                         details={"doctor_id": str(doctor_id), "kind": kind, "on_date": on_date.isoformat()})
    db.session.commit()
    return exc


def delete_exception(actor: Actor, hospital_id: uuid.UUID, exception_id: uuid.UUID) -> None:
    require(actor, Perm.SCHEDULES_MANAGE, hospital_id)
    exc = db.session.get(DoctorScheduleException, exception_id)
    if exc is None or exc.hospital_id != hospital_id:
        raise NotFound("Schedule exception not found.")
    audit_service.record("schedule.exception_deleted", actor=actor, hospital_id=hospital_id,
                         target_type="schedule_exception", target_id=exc.id,
                         details={"doctor_id": str(exc.doctor_id), "kind": exc.kind, "on_date": exc.on_date.isoformat()})
    db.session.delete(exc)
    db.session.commit()


# --- Slot generation -------------------------------------------------------------------


def _session_slots(on_date: date, start: time, end: time, minutes: int, tz) -> list[Slot]:
    slots = []
    cursor = datetime.combine(on_date, start, tzinfo=tz)
    session_end = datetime.combine(on_date, end, tzinfo=tz)
    step = timedelta(minutes=minutes)
    while cursor + step <= session_end:
        slots.append(Slot(cursor.astimezone(UTC), (cursor + step).astimezone(UTC)))
        cursor += step
    return slots


def generate_slots(doctor: DoctorProfile, on_date: date) -> list[Slot]:
    """All slots the doctor's schedule offers on a hospital-local date, ignoring bookings."""
    tz = hospital_tz(doctor.hospital)
    templates = db.session.scalars(select(DoctorScheduleTemplate).where(
        DoctorScheduleTemplate.doctor_id == doctor.id,
        DoctorScheduleTemplate.is_active.is_(True),
        DoctorScheduleTemplate.weekday == on_date.weekday(),
        DoctorScheduleTemplate.valid_from <= on_date,
        or_(DoctorScheduleTemplate.valid_until.is_(None), DoctorScheduleTemplate.valid_until >= on_date),
    )).all()
    exceptions = db.session.scalars(select(DoctorScheduleException).where(
        DoctorScheduleException.doctor_id == doctor.id, DoctorScheduleException.on_date == on_date)).all()

    slots: dict[datetime, Slot] = {}
    for t in templates:
        for s in _session_slots(on_date, t.start_time, t.end_time, t.slot_minutes, tz):
            slots.setdefault(s.start_at, s)
    for e in exceptions:
        if e.kind == "extra_session":
            for s in _session_slots(on_date, e.start_time, e.end_time, e.slot_minutes, tz):
                slots.setdefault(s.start_at, s)

    blocked = []
    for e in exceptions:
        if e.kind == "unavailable":
            if e.start_time is None:
                return []
            blocked.append((datetime.combine(on_date, e.start_time, tzinfo=tz).astimezone(UTC),
                            datetime.combine(on_date, e.end_time, tzinfo=tz).astimezone(UTC)))
    result = [s for s in slots.values() if not any(s.start_at < b_end and s.end_at > b_start for b_start, b_end in blocked)]
    return sorted(result, key=lambda s: s.start_at)


def held_ranges(doctor_id: uuid.UUID, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    rows = db.session.execute(select(Appointment.status, Appointment.start_at, Appointment.end_at,
                                     Appointment.proposed_start_at, Appointment.proposed_end_at).where(
        Appointment.doctor_id == doctor_id,
        or_(
            and_(Appointment.status.in_([AppointmentStatus.APPROVED, AppointmentStatus.CHECKED_IN]),
                 Appointment.start_at < end, Appointment.end_at > start),
            and_(Appointment.status == AppointmentStatus.RESCHEDULE_PROPOSED,
                 Appointment.proposed_start_at < end, Appointment.proposed_end_at > start),
        ),
    )).all()
    ranges = []
    for status, s, e, ps, pe in rows:
        ranges.append((ps, pe) if status == AppointmentStatus.RESCHEDULE_PROPOSED else (s, e))
    return ranges


@dataclass(frozen=True)
class SlotView:
    start_at: datetime
    end_at: datetime
    available: bool


def slots_with_availability(doctor: DoctorProfile, on_date: date, *, for_patient: bool) -> list[SlotView]:
    """Slots for a date with availability. Patients only see slots inside the booking window."""
    slots = generate_slots(doctor, on_date)
    if not slots:
        return []
    held = held_ranges(doctor.id, slots[0].start_at, slots[-1].end_at)
    earliest, latest = booking_window(doctor, for_patient=for_patient)
    out = []
    for s in slots:
        free = not any(s.start_at < h_end and s.end_at > h_start for h_start, h_end in held)
        in_window = earliest <= s.start_at <= latest
        out.append(SlotView(s.start_at, s.end_at, free and in_window))
    return out


def booking_window(doctor: DoctorProfile, *, for_patient: bool) -> tuple[datetime, datetime]:
    now = utcnow()
    if not for_patient:
        return now, now + timedelta(days=366)
    hospital = doctor.hospital
    return (now + timedelta(minutes=get_setting(hospital, "booking_min_lead_minutes")),
            now + timedelta(days=get_setting(hospital, "booking_max_days_ahead")))


def resolve_slot(doctor: DoctorProfile, start_at: datetime, *, for_patient: bool) -> Slot:
    """Validate that `start_at` is a real slot in the doctor's schedule and inside the booking window."""
    if start_at.tzinfo is None:
        raise ValidationFailed(details={"start_at": ["Include a timezone offset."]})
    start_at = start_at.astimezone(UTC)
    earliest, latest = booking_window(doctor, for_patient=for_patient)
    if start_at < earliest:
        raise Conflict("This time is too soon or already in the past.", code="slot_too_soon")
    if start_at > latest:
        raise Conflict("This time is too far in the future to book.", code="slot_too_far_ahead")
    local_date = start_at.astimezone(hospital_tz(doctor.hospital)).date()
    for slot in generate_slots(doctor, local_date):
        if slot.start_at == start_at:
            return slot
    raise Conflict("The doctor has no appointment slot at this time.", code="not_a_slot")


def get_public_slots(doctor_id: uuid.UUID, on_date: date) -> tuple[DoctorProfile, list[SlotView]]:
    from careflow.services.hospital_service import get_doctor

    doctor = get_doctor(doctor_id)
    if not doctor.is_accepting_appointments:
        return doctor, []
    return doctor, slots_with_availability(doctor, on_date, for_patient=True)
