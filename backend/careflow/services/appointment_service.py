"""Appointment lifecycle: requests, staff review, rescheduling, cancellation, expiry.

Rules enforced here (and by database constraints):
- A patient submission is only a *request*; staff must approve it.
- Times must match a real slot in the doctor's schedule.
- Approved appointments hold their time; overlapping holds are rejected by an exclusion
  constraint, so concurrent approvals of the same slot cannot both succeed.
- Every status change goes through `appointment_rules.check_transition`, is written to the
  append-only status history, and is audited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from careflow.errors import Conflict, Forbidden, NotFound, ValidationFailed
from careflow.extensions import db
from careflow.models import (
    Appointment,
    AppointmentStatus as S,
    AppointmentStatusHistory,
    DoctorProfile,
    HospitalPatientRecord,
)
from careflow.models.base import utcnow
from careflow.realtime import publish
from careflow.security.policy import Actor, Perm, require, require_patient
from careflow.services import audit_service, notification_service
from careflow.services.appointment_rules import check_transition
from careflow.services.hospital_service import get_doctor, get_hospital
from careflow.services.hospital_settings import get_setting, hospital_tz
from careflow.services.patient_service import ensure_hospital_record
from careflow.services.scheduling_service import held_ranges, resolve_slot

ActorKind = str  # "patient" | "staff" | "doctor"


# --- Access ------------------------------------------------------------------------------


def actor_kind_for(actor: Actor, appt: Appointment) -> ActorKind | None:
    """How the caller relates to this appointment, or None if they may not see it."""
    if actor.patient_id is not None and actor.patient_id == appt.patient_id:
        return "patient"
    if actor.can(Perm.APPOINTMENTS_REVIEW, appt.hospital_id):
        return "staff"
    if actor.doctor_ids.get(appt.hospital_id) == appt.doctor_id:
        return "doctor"
    return None


def _load(actor: Actor, appointment_id: uuid.UUID, *, for_update: bool = False) -> tuple[Appointment, ActorKind]:
    stmt = select(Appointment).where(Appointment.id == appointment_id)
    if for_update:
        stmt = stmt.with_for_update()
    appt = db.session.scalar(stmt)
    kind = actor_kind_for(actor, appt) if appt else None
    if appt is None or kind is None:
        raise NotFound("Appointment not found.")
    return appt, kind


def _require_kind(kind: ActorKind, *allowed: ActorKind) -> None:
    if kind not in allowed:
        raise Forbidden()


def _check_version(appt: Appointment, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != appt.version:
        raise Conflict("This appointment was changed by someone else. Reload and try again.", code="stale_version",
                       details={"current_version": appt.version})


# --- Status changes ---------------------------------------------------------------------


def _fmt_local(appt_time: datetime, appt: Appointment) -> str:
    local = appt_time.astimezone(hospital_tz(appt.hospital))
    return local.strftime("%a %d %b %Y, %H:%M").replace(" 0", " ", 1)


def _doctor_name(doctor: DoctorProfile) -> str:
    return f"{doctor.title} {doctor.user.full_name}" if doctor.title else doctor.user.full_name


def _record(appt: Appointment, *, event: str, from_status: str | None, actor: Actor | None, actor_role: str,
            reason: str | None = None, details: dict | None = None) -> None:
    db.session.add(AppointmentStatusHistory(
        appointment_id=appt.id, hospital_id=appt.hospital_id, event=event, from_status=from_status,
        to_status=appt.status, actor_user_id=actor.user_id if actor else None, actor_role=actor_role,
        reason=reason, details=details or {}))
    audit_service.record(f"appointment.{event}", actor=actor, hospital_id=appt.hospital_id,
                         target_type="appointment", target_id=appt.id, reason=reason,
                         details={"from_status": from_status, "to_status": appt.status, **(details or {})})
    publish("appointment", action=event, hospital_id=appt.hospital_id, appointment_id=appt.id,
            doctor_id=appt.doctor_id, patient_id=appt.patient_id, status=appt.status)


_PATIENT_MESSAGES = {
    "requested": ("Appointment request received",
                  "Your request for {when} with {doctor} was received. It is not confirmed until the hospital approves it."),
    "booked_by_staff": ("Appointment booked", "The hospital booked an appointment for you on {when} with {doctor}."),
    "approved": ("Appointment approved", "Your appointment on {when} with {doctor} is confirmed."),
    "rejected": ("Appointment request not approved",
                 "Your request for {when} with {doctor} was not approved. Reason: {reason}"),
    "reschedule_proposed": ("New time proposed",
                            "The hospital proposed {proposed} with {doctor} instead of {when}. Please accept or decline."),
    "rescheduled": ("Appointment rescheduled", "Your appointment is now on {when} with {doctor}."),
    "cancelled_by_staff": ("Appointment cancelled",
                           "Your appointment on {when} with {doctor} was cancelled by the hospital. Reason: {reason}"),
    "expired": ("Appointment request expired",
                "Your request for {when} with {doctor} expired before it could be confirmed. You can request another time."),
    "no_show": ("Missed appointment", "You were marked as not attending your appointment on {when} with {doctor}."),
}


def _notify_patient(appt: Appointment, message_key: str, reason: str | None = None) -> None:
    title, template = _PATIENT_MESSAGES[message_key]
    body = template.format(
        when=_fmt_local(appt.start_at, appt), doctor=_doctor_name(appt.doctor), reason=reason or "not given",
        proposed=_fmt_local(appt.proposed_start_at, appt) if appt.proposed_start_at else "",
    )
    notification_service.notify(user_id=appt.patient.user_id, hospital_id=appt.hospital_id, type=f"appointment.{message_key}",
                                title=title, body=body, related_type="appointment", related_id=appt.id)


def _apply(appt: Appointment, event: str, actor_kind: str, reason: str | None) -> str:
    from_status = appt.status
    appt.status = check_transition(event, from_status, actor_kind, reason)
    appt.version += 1
    if reason is not None:
        appt.status_reason = reason.strip() or None
    return from_status


def _flush_or_slot_conflict() -> None:
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
        if constraint == "ex_appointments_patient_no_overlap":
            raise Conflict("The patient already has an appointment at this time.", code="patient_time_conflict") from None
        if constraint == "ex_appointments_doctor_no_overlap":
            raise Conflict("This time slot is no longer available.", code="slot_unavailable") from None
        if constraint == "uq_appointments_open_request":
            raise Conflict("You already requested this time with this doctor.", code="duplicate_request") from None
        raise


# --- Patient requests -------------------------------------------------------------------


@dataclass
class RequestResult:
    appointment: Appointment
    created: bool


def request_appointment(actor: Actor, *, doctor_id: uuid.UUID, start_at: datetime, department_id: uuid.UUID | None,
                        reason_for_visit: str | None, idempotency_key: str | None) -> RequestResult:
    patient_id = require_patient(actor)

    if idempotency_key:
        existing = db.session.scalar(select(Appointment).where(
            Appointment.patient_id == patient_id, Appointment.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.doctor_id != doctor_id or existing.start_at != start_at.astimezone(UTC):
                raise Conflict("This Idempotency-Key was already used for a different request.",
                               code="idempotency_key_reused")
            return RequestResult(existing, False)

    doctor = get_doctor(doctor_id)
    if department_id is not None and department_id != doctor.department_id:
        raise ValidationFailed(details={"department_id": ["The doctor does not belong to this department."]})
    if not doctor.is_accepting_appointments:
        raise Conflict("This doctor is not accepting appointment requests.", code="doctor_not_accepting")
    slot = resolve_slot(doctor, start_at, for_patient=True)
    if held_ranges(doctor.id, slot.start_at, slot.end_at):
        raise Conflict("This time slot is no longer available.", code="slot_unavailable")

    hospital = doctor.hospital
    open_count = db.session.scalar(select(func.count()).select_from(Appointment).where(
        Appointment.patient_id == patient_id, Appointment.hospital_id == hospital.id,
        Appointment.status.in_([S.REQUESTED, S.RESCHEDULE_PROPOSED])))
    if open_count >= get_setting(hospital, "max_open_requests_per_patient"):
        raise Conflict("You have too many requests waiting for review at this hospital.", code="too_many_open_requests")

    ensure_hospital_record(hospital.id, patient_id, actor.user_id)
    appt = Appointment(hospital_id=hospital.id, department_id=doctor.department_id, doctor_id=doctor.id,
                       patient_id=patient_id, start_at=slot.start_at, end_at=slot.end_at, status=S.REQUESTED,
                       source="patient_request", reason_for_visit=reason_for_visit, idempotency_key=idempotency_key,
                       created_by_user_id=actor.user_id)
    db.session.add(appt)
    try:
        _flush_or_slot_conflict()
    except IntegrityError:
        # Lost an idempotency race with an identical retry: return the winner.
        if idempotency_key:
            winner = db.session.scalar(select(Appointment).where(
                Appointment.patient_id == patient_id, Appointment.idempotency_key == idempotency_key))
            if winner is not None:
                return RequestResult(winner, False)
        raise
    _record(appt, event="requested", from_status=None, actor=actor, actor_role="patient")
    _notify_patient(appt, "requested")
    db.session.commit()
    return RequestResult(appt, True)


def staff_book(actor: Actor, hospital_id: uuid.UUID, *, patient_id: uuid.UUID, doctor_id: uuid.UUID,
               start_at: datetime, reason_for_visit: str | None) -> Appointment:
    """Book directly on a patient's behalf (e.g. by phone). Created already approved."""
    get_hospital(hospital_id)
    require(actor, Perm.APPOINTMENTS_REVIEW, hospital_id)
    if not db.session.scalar(select(HospitalPatientRecord.id).where(
            HospitalPatientRecord.hospital_id == hospital_id, HospitalPatientRecord.patient_id == patient_id)):
        raise NotFound("Patient not found.")
    doctor = db.session.get(DoctorProfile, doctor_id)
    if doctor is None or doctor.hospital_id != hospital_id or not doctor.is_active:
        raise NotFound("Doctor not found.")
    slot = resolve_slot(doctor, start_at, for_patient=False)
    appt = Appointment(hospital_id=hospital_id, department_id=doctor.department_id, doctor_id=doctor.id,
                       patient_id=patient_id, start_at=slot.start_at, end_at=slot.end_at, status=S.APPROVED,
                       source="staff_booked", reason_for_visit=reason_for_visit, created_by_user_id=actor.user_id,
                       reviewed_by_user_id=actor.user_id, reviewed_at=utcnow())
    db.session.add(appt)
    _flush_or_slot_conflict()
    _record(appt, event="booked_by_staff", from_status=None, actor=actor, actor_role=_staff_role(actor, hospital_id))
    _notify_patient(appt, "booked_by_staff")
    db.session.commit()
    return appt


def _staff_role(actor: Actor, hospital_id: uuid.UUID) -> str:
    roles = actor.roles_in(hospital_id)
    for role in ("receptionist", "hospital_admin", "doctor"):
        if role in roles:
            return role
    return "staff"


# --- Staff review ----------------------------------------------------------------------


def _staff_action(actor: Actor, appointment_id: uuid.UUID, expected_version: int | None) -> Appointment:
    appt, kind = _load(actor, appointment_id, for_update=True)
    _require_kind(kind, "staff")
    _check_version(appt, expected_version)
    return appt


def approve(actor: Actor, appointment_id: uuid.UUID, *, expected_version: int | None = None) -> Appointment:
    appt = _staff_action(actor, appointment_id, expected_version)
    if appt.status == S.REQUESTED:
        # The doctor's schedule may have changed since the request; the slot must still exist.
        resolve_slot(appt.doctor, appt.start_at, for_patient=False)
    from_status = _apply(appt, "approve", "staff", None)
    appt.reviewed_by_user_id, appt.reviewed_at = actor.user_id, utcnow()
    _flush_or_slot_conflict()
    _record(appt, event="approved", from_status=from_status, actor=actor, actor_role=_staff_role(actor, appt.hospital_id))
    _notify_patient(appt, "approved")
    db.session.commit()
    return appt


def reject(actor: Actor, appointment_id: uuid.UUID, *, reason: str, expected_version: int | None = None) -> Appointment:
    appt = _staff_action(actor, appointment_id, expected_version)
    from_status = _apply(appt, "reject", "staff", reason)
    appt.reviewed_by_user_id, appt.reviewed_at = actor.user_id, utcnow()
    _record(appt, event="rejected", from_status=from_status, actor=actor,
            actor_role=_staff_role(actor, appt.hospital_id), reason=reason)
    _notify_patient(appt, "rejected", reason)
    db.session.commit()
    return appt


def propose_reschedule(actor: Actor, appointment_id: uuid.UUID, *, new_start_at: datetime, reason: str,
                       expected_version: int | None = None) -> Appointment:
    """Offer the patient a different time with the same doctor. The proposed time is held while pending;
    the original time is released."""
    appt = _staff_action(actor, appointment_id, expected_version)
    slot = resolve_slot(appt.doctor, new_start_at, for_patient=False)
    if slot.start_at == appt.start_at:
        raise ValidationFailed(details={"start_at": ["Choose a different time from the current one."]})
    appt.proposed_start_at, appt.proposed_end_at = slot.start_at, slot.end_at
    from_status = _apply(appt, "propose_reschedule", "staff", reason)
    appt.reviewed_by_user_id, appt.reviewed_at = actor.user_id, utcnow()
    _flush_or_slot_conflict()
    _record(appt, event="reschedule_proposed", from_status=from_status, actor=actor,
            actor_role=_staff_role(actor, appt.hospital_id), reason=reason,
            details={"proposed_start_at": slot.start_at.isoformat()})
    _notify_patient(appt, "reschedule_proposed", reason)
    db.session.commit()
    return appt


def mark_no_show(actor: Actor, appointment_id: uuid.UUID, *, reason: str | None,
                 expected_version: int | None = None) -> Appointment:
    appt = _staff_action(actor, appointment_id, expected_version)
    grace = timedelta(minutes=get_setting(appt.hospital, "no_show_grace_minutes"))
    if utcnow() < appt.start_at + grace:
        raise Conflict("It is too early to mark this appointment as missed.", code="too_early_for_no_show")
    from_status = _apply(appt, "mark_no_show", "staff", reason)
    _record(appt, event="no_show", from_status=from_status, actor=actor,
            actor_role=_staff_role(actor, appt.hospital_id), reason=reason)
    _notify_patient(appt, "no_show")
    db.session.commit()
    return appt


# --- Patient responses and cancellation -----------------------------------------------


def accept_reschedule(actor: Actor, appointment_id: uuid.UUID) -> Appointment:
    appt, kind = _load(actor, appointment_id, for_update=True)
    _require_kind(kind, "patient")
    if appt.status == S.RESCHEDULE_PROPOSED and appt.proposed_start_at <= utcnow():
        raise Conflict("The proposed time has already passed.", code="proposal_expired")
    previous_start = appt.start_at
    from_status = appt.status
    check_transition("accept_reschedule", from_status, "patient")
    # Move the proposal into the appointment; the held range stays the same.
    appt.start_at, appt.end_at = appt.proposed_start_at, appt.proposed_end_at
    appt.proposed_start_at = appt.proposed_end_at = None
    _apply(appt, "accept_reschedule", "patient", None)
    _flush_or_slot_conflict()
    _record(appt, event="rescheduled", from_status=from_status, actor=actor, actor_role="patient",
            details={"previous_start_at": previous_start.isoformat()})
    _notify_patient(appt, "rescheduled")
    db.session.commit()
    return appt


def decline_reschedule(actor: Actor, appointment_id: uuid.UUID, *, reason: str | None) -> Appointment:
    appt, kind = _load(actor, appointment_id, for_update=True)
    _require_kind(kind, "patient")
    from_status = _apply(appt, "decline_reschedule", "patient", reason)
    _record(appt, event="reschedule_declined", from_status=from_status, actor=actor, actor_role="patient", reason=reason)
    db.session.commit()
    return appt


def cancel(actor: Actor, appointment_id: uuid.UUID, *, reason: str | None,
           expected_version: int | None = None) -> Appointment:
    appt, kind = _load(actor, appointment_id, for_update=True)
    _require_kind(kind, "patient", "staff")
    _check_version(appt, expected_version)
    if kind == "patient":
        if appt.status == S.APPROVED:
            cutoff = appt.start_at - timedelta(minutes=get_setting(appt.hospital, "patient_cancel_min_minutes"))
            if utcnow() > cutoff:
                raise Conflict("This appointment can no longer be cancelled online. Please contact the hospital.",
                               code="cancellation_window_closed")
        from_status = _apply(appt, "cancel_by_patient", "patient", reason)
        _record(appt, event="cancelled_by_patient", from_status=from_status, actor=actor, actor_role="patient",
                reason=reason)
    else:
        from_status = _apply(appt, "cancel_by_staff", "staff", reason)
        _record(appt, event="cancelled_by_staff", from_status=from_status, actor=actor,
                actor_role=_staff_role(actor, appt.hospital_id), reason=reason)
        _notify_patient(appt, "cancelled_by_staff", reason)
    db.session.commit()
    return appt


# --- Driven by the queue (check-in, completion, leaving the queue) ----------------------


def apply_queue_transition(appt: Appointment, event: str, *, actor: Actor | None, actor_kind: str, actor_role: str,
                           history_event: str, reason: str | None = None) -> None:
    """Change an appointment's status as a side effect of a queue action. Caller holds the locks and commits."""
    from_status = _apply(appt, event, actor_kind, reason)
    _record(appt, event=history_event, from_status=from_status, actor=actor, actor_role=actor_role, reason=reason)


def lock_for_queue(appointment_id: uuid.UUID) -> Appointment:
    return db.session.scalar(select(Appointment).where(Appointment.id == appointment_id).with_for_update()
                             .execution_options(populate_existing=True))


# --- Expiry ----------------------------------------------------------------------------


def expire_stale(*, hospital_id: uuid.UUID | None = None, patient_id: uuid.UUID | None = None) -> int:
    """Expire requests nobody reviewed before their time, and proposals the patient never answered.

    Safe to call concurrently: rows locked by another transaction are skipped.
    """
    now = utcnow()
    stmt = select(Appointment).where(or_(
        and_(Appointment.status == S.REQUESTED, Appointment.start_at <= now),
        and_(Appointment.status == S.RESCHEDULE_PROPOSED, Appointment.proposed_start_at <= now),
    )).with_for_update(skip_locked=True)
    if hospital_id:
        stmt = stmt.where(Appointment.hospital_id == hospital_id)
    if patient_id:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    rows = db.session.scalars(stmt).all()
    for appt in rows:
        from_status = _apply(appt, "expire", "system", None)
        _record(appt, event="expired", from_status=from_status, actor=None, actor_role="system")
        _notify_patient(appt, "expired")
    db.session.commit()
    return len(rows)


# --- Queries ---------------------------------------------------------------------------


def get(actor: Actor, appointment_id: uuid.UUID) -> tuple[Appointment, ActorKind]:
    return _load(actor, appointment_id)


def history(actor: Actor, appointment_id: uuid.UUID) -> tuple[list[AppointmentStatusHistory], ActorKind]:
    appt, kind = _load(actor, appointment_id)
    rows = db.session.scalars(select(AppointmentStatusHistory)
                              .where(AppointmentStatusHistory.appointment_id == appt.id)
                              .order_by(AppointmentStatusHistory.occurred_at, AppointmentStatusHistory.id)).all()
    return list(rows), kind


def list_mine(actor: Actor, *, statuses: list[str] | None, limit: int,
              cursor: tuple[datetime, uuid.UUID] | None) -> list[Appointment]:
    patient_id = require_patient(actor)
    expire_stale(patient_id=patient_id)
    stmt = select(Appointment).where(Appointment.patient_id == patient_id)
    if statuses:
        stmt = stmt.where(Appointment.status.in_(statuses))
    if cursor:
        ts, last_id = cursor
        stmt = stmt.where(or_(Appointment.start_at < ts, and_(Appointment.start_at == ts, Appointment.id < last_id)))
    return list(db.session.scalars(stmt.order_by(Appointment.start_at.desc(), Appointment.id.desc()).limit(limit)))


def list_for_hospital(actor: Actor, hospital_id: uuid.UUID, *, statuses: list[str] | None,
                      doctor_id: uuid.UUID | None, department_id: uuid.UUID | None, patient_id: uuid.UUID | None,
                      date_from: date | None, date_to: date | None, limit: int,
                      cursor: tuple[datetime, uuid.UUID] | None) -> tuple[list[Appointment], ActorKind]:
    """Staff see the hospital's appointments; doctors see only their own."""
    hospital = get_hospital(hospital_id)
    if actor.can(Perm.APPOINTMENTS_REVIEW, hospital_id):
        kind = "staff"
    elif hospital_id in actor.doctor_ids:
        kind = "doctor"
        if doctor_id is not None and doctor_id != actor.doctor_ids[hospital_id]:
            raise Forbidden("Doctors can only view their own appointments.")
        doctor_id = actor.doctor_ids[hospital_id]
    else:
        raise Forbidden()
    expire_stale(hospital_id=hospital_id)

    tz = hospital_tz(hospital)
    stmt = select(Appointment).where(Appointment.hospital_id == hospital_id)
    if statuses:
        stmt = stmt.where(Appointment.status.in_(statuses))
    if doctor_id:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    if department_id:
        stmt = stmt.where(Appointment.department_id == department_id)
    if patient_id:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    if date_from:
        stmt = stmt.where(Appointment.start_at >= datetime.combine(date_from, time.min, tzinfo=tz))
    if date_to:
        stmt = stmt.where(Appointment.start_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz))
    if cursor:
        ts, last_id = cursor
        stmt = stmt.where(or_(Appointment.start_at > ts, and_(Appointment.start_at == ts, Appointment.id > last_id)))
    rows = db.session.scalars(stmt.order_by(Appointment.start_at, Appointment.id).limit(limit)).all()
    return list(rows), kind


def hospital_patient_numbers(appts: list[Appointment]) -> dict[tuple[uuid.UUID, uuid.UUID], str]:
    """(hospital_id, patient_id) -> hospital patient number, fetched in one query."""
    keys = {(a.hospital_id, a.patient_id) for a in appts}
    if not keys:
        return {}
    rows = db.session.execute(select(HospitalPatientRecord.hospital_id, HospitalPatientRecord.patient_id,
                                     HospitalPatientRecord.hospital_patient_number).where(
        HospitalPatientRecord.patient_id.in_({p for _, p in keys}),
        HospitalPatientRecord.hospital_id.in_({h for h, _ in keys}))).all()
    return {(h, p): n for h, p, n in rows}

