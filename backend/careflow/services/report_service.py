"""Dashboard summary and exportable operational reports for hospital staff."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from careflow.errors import Forbidden, ValidationFailed
from careflow.extensions import db
from careflow.models import (
    Appointment,
    AppointmentStatus,
    AuditEvent,
    DoctorProfile,
    HospitalPatientRecord,
    Queue,
    QueueEntry,
    QueueEvent,
)
from careflow.security.policy import Actor, Perm, require
from careflow.services import audit_service
from careflow.services import availability_service as availability
from careflow.services.hospital_service import get_hospital
from careflow.services.hospital_settings import hospital_tz

# Queue events worth surfacing as operational alerts on the dashboard.
ALERT_EVENTS = ("missed", "paused", "resumed", "priority_changed", "cancelled", "transferred_out", "returned")
EXPORT_ROW_LIMIT = 10_000


def _require_desk(actor: Actor, hospital_id: uuid.UUID) -> None:
    if not (actor.can(Perm.QUEUE_MANAGE, hospital_id) or actor.can(Perm.APPOINTMENTS_REVIEW, hospital_id)):
        raise Forbidden()


def dashboard(actor: Actor, hospital_id: uuid.UUID) -> dict:
    hospital = get_hospital(hospital_id)
    _require_desk(actor, hospital_id)
    today = availability.local_today(hospital)
    day_start, day_end = availability.today_window(hospital)

    queue_counts = dict(db.session.execute(
        select(QueueEntry.status, func.count()).join(Queue, Queue.id == QueueEntry.queue_id)
        .where(Queue.hospital_id == hospital_id, Queue.service_date == today).group_by(QueueEntry.status)).all())
    appt_counts = dict(db.session.execute(
        select(Appointment.status, func.count()).where(
            Appointment.hospital_id == hospital_id,
            Appointment.status.in_([AppointmentStatus.REQUESTED, AppointmentStatus.RESCHEDULE_PROPOSED]))
        .group_by(Appointment.status)).all())
    todays_appointments = db.session.scalar(select(func.count()).select_from(Appointment).where(
        Appointment.hospital_id == hospital_id, Appointment.start_at >= day_start, Appointment.start_at < day_end,
        Appointment.status.in_([AppointmentStatus.APPROVED, AppointmentStatus.CHECKED_IN, AppointmentStatus.COMPLETED,
                                AppointmentStatus.NO_SHOW])))

    doctors = db.session.scalars(select(DoctorProfile).where(
        DoctorProfile.hospital_id == hospital_id, DoctorProfile.is_active.is_(True))).all()
    doctor_counts: dict[str, int] = {}
    for d in doctors:
        label = availability.public_status(d)
        doctor_counts[label] = doctor_counts.get(label, 0) + 1

    alert_rows = db.session.execute(
        select(QueueEvent, QueueEntry.ticket_number, Queue.doctor_id)
        .join(Queue, Queue.id == QueueEvent.queue_id)
        .outerjoin(QueueEntry, QueueEntry.id == QueueEvent.entry_id)
        .where(QueueEvent.hospital_id == hospital_id, QueueEvent.event_type.in_(ALERT_EVENTS),
               QueueEvent.occurred_at >= day_start)
        .order_by(QueueEvent.occurred_at.desc()).limit(20)).all()
    doctor_names = {d.id: (f"{d.title} {d.user.full_name}" if d.title else d.user.full_name) for d in doctors}
    alerts = [{"event_type": ev.event_type, "ticket_number": ticket, "doctor_name": doctor_names.get(doctor_id),
               "queue_id": ev.queue_id, "entry_id": ev.entry_id, "reason": ev.reason, "occurred_at": ev.occurred_at}
              for ev, ticket, doctor_id in alert_rows]

    return {
        "date": today,
        "timezone": hospital.timezone,
        "queue": {
            "waiting": queue_counts.get("waiting", 0),
            "called": queue_counts.get("called", 0),
            "in_consultation": queue_counts.get("in_consultation", 0),
            "completed": queue_counts.get("completed", 0),
            "missed": queue_counts.get("missed", 0),
            "cancelled": queue_counts.get("cancelled", 0),
        },
        "appointments": {
            "pending_approval": appt_counts.get(AppointmentStatus.REQUESTED, 0),
            "awaiting_patient_response": appt_counts.get(AppointmentStatus.RESCHEDULE_PROPOSED, 0),
            "today": todays_appointments,
        },
        "doctors": doctor_counts,
        "alerts": alerts,
    }


def _fmt(dt: datetime | None, tz) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else ""


def _minutes(a: datetime | None, b: datetime | None) -> str:
    return str(round((b - a).total_seconds() / 60)) if a and b else ""


def queue_day_csv(actor: Actor, hospital_id: uuid.UUID, service_date: date) -> str:
    """One row per queue entry for a day. Identifies patients by hospital number only (no names)."""
    hospital = get_hospital(hospital_id)
    require(actor, Perm.AUDIT_EXPORT, hospital_id)
    tz = hospital_tz(hospital)
    rows = db.session.execute(
        select(QueueEntry, Queue.doctor_id, HospitalPatientRecord.hospital_patient_number)
        .join(Queue, Queue.id == QueueEntry.queue_id)
        .outerjoin(HospitalPatientRecord, (HospitalPatientRecord.hospital_id == QueueEntry.hospital_id)
                   & (HospitalPatientRecord.patient_id == QueueEntry.patient_id))
        .where(Queue.hospital_id == hospital_id, Queue.service_date == service_date)
        .order_by(Queue.doctor_id, QueueEntry.ticket_number).limit(EXPORT_ROW_LIMIT)).all()
    doctors = {d.id: (f"{d.title} {d.user.full_name}" if d.title else d.user.full_name)
               for d in db.session.scalars(select(DoctorProfile).where(DoctorProfile.hospital_id == hospital_id))}

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["service_date", "doctor", "ticket", "kind", "status", "hospital_patient_number", "priority_level",
                "checked_in", "called", "consultation_started", "completed", "wait_minutes", "consultation_minutes",
                "initial_estimate_min", "initial_estimate_max", "missed_count"])
    for e, doctor_id, number in rows:
        w.writerow([service_date.isoformat(), doctors.get(doctor_id, ""), e.ticket_number, e.kind, e.status,
                    number or "", e.priority_level, _fmt(e.checked_in_at, tz), _fmt(e.called_at, tz),
                    _fmt(e.started_at, tz), _fmt(e.completed_at, tz), _minutes(e.entered_queue_at, e.called_at),
                    _minutes(e.started_at, e.completed_at), e.initial_estimate_min or "", e.initial_estimate_max or "",
                    e.missed_count])
    audit_service.record("report.exported", actor=actor, hospital_id=hospital_id,
                         details={"report": "queue_day", "service_date": service_date.isoformat(), "rows": len(rows)})
    db.session.commit()
    return out.getvalue()


def audit_csv(actor: Actor, hospital_id: uuid.UUID, *, occurred_from: datetime | None, occurred_to: datetime | None,
              action: str | None, target_type: str | None) -> str:
    hospital = get_hospital(hospital_id)
    require(actor, Perm.AUDIT_VIEW, hospital_id)
    require(actor, Perm.AUDIT_EXPORT, hospital_id)
    if occurred_from and occurred_to and occurred_to <= occurred_from:
        raise ValidationFailed(details={"occurred_to": ["Must be after occurred_from."]})
    tz = hospital_tz(hospital)
    occurred_from = occurred_from or datetime.combine(availability.local_today(hospital) - timedelta(days=30),
                                                      time.min, tzinfo=tz)
    stmt = select(AuditEvent).where(AuditEvent.hospital_id == hospital_id, AuditEvent.occurred_at >= occurred_from)
    if occurred_to:
        stmt = stmt.where(AuditEvent.occurred_at < occurred_to)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    events = db.session.scalars(stmt.order_by(AuditEvent.occurred_at).limit(EXPORT_ROW_LIMIT)).all()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["occurred_at_local", "action", "actor_user_id", "actor_roles", "target_type", "target_id", "reason",
                "request_id"])
    for ev in events:
        w.writerow([_fmt(ev.occurred_at, tz), ev.action, ev.actor_user_id or "", ev.actor_roles or "",
                    ev.target_type or "", ev.target_id or "", ev.reason or "", ev.request_id or ""])
    audit_service.record("report.exported", actor=actor, hospital_id=hospital_id,
                         details={"report": "audit", "rows": len(events)})
    db.session.commit()
    return out.getvalue()
