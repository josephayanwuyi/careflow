"""Queue engine: check-in, walk-ins, calling, consultations, missed turns, transfers, priority.

Concurrency: every operation that changes a queue first locks that queue's row
(`SELECT ... FOR UPDATE`), so changes to one queue are applied one at a time across all API
workers. Database constraints back this up: one live entry per appointment, one live place
per patient per day, unique ticket numbers per queue, and at most one patient called or in
consultation per queue (which makes duplicate "call next" impossible).

Rules: see services/queue_rules.py. Every transition writes an append-only QueueEvent,
an audit record, and a real-time notification.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from careflow.errors import Conflict, Forbidden, NotFound, ValidationFailed
from careflow.extensions import db
from careflow.models import (
    Appointment,
    AppointmentStatus,
    ConsultationSession,
    DoctorProfile,
    DoctorStatus,
    Hospital,
    HospitalPatientRecord,
    Queue,
    QueueEntry,
    QueueEvent,
    QueueStatus as Q,
    User,
)
from careflow.models.base import utcnow
from careflow.realtime import publish
from careflow.security.policy import Actor, Perm
from careflow.services import appointment_service, audit_service, notification_service
from careflow.services import availability_service as availability
from careflow.services.appointment_rules import check_transition as check_appointment_transition
from careflow.services.estimator import Estimate, EstimateInputs, duration_stats, estimate
from careflow.services.hospital_service import get_hospital
from careflow.services.hospital_settings import get_setting, hospital_tz
from careflow.services.queue_rules import check_transition, initial_sort_key, ordered_waiting

ActorKind = str  # staff | doctor | patient


# --- Locking and access ------------------------------------------------------------------


def _lock_queue(queue_id: uuid.UUID) -> Queue:
    return db.session.scalar(select(Queue).where(Queue.id == queue_id).with_for_update()
                             .execution_options(populate_existing=True))


def _lock_entry(entry_id: uuid.UUID) -> QueueEntry:
    return db.session.scalar(select(QueueEntry).where(QueueEntry.id == entry_id).with_for_update()
                             .execution_options(populate_existing=True))


def _ensure_queue_id(doctor: DoctorProfile, service_date: date) -> uuid.UUID:
    """Create the doctor's queue for the date if needed (safe if two requests race) and return its id."""
    db.session.execute(pg_insert(Queue).values(
        id=uuid.uuid4(), hospital_id=doctor.hospital_id, department_id=doctor.department_id, doctor_id=doctor.id,
        service_date=service_date, is_paused=False, next_ticket_number=1,
    ).on_conflict_do_nothing(index_elements=["doctor_id", "service_date"]))
    return db.session.scalar(select(Queue.id).where(Queue.doctor_id == doctor.id, Queue.service_date == service_date))


def _get_or_create_queue(doctor: DoctorProfile, service_date: date) -> Queue:
    """Return the doctor's queue for the date, locked."""
    return _lock_queue(_ensure_queue_id(doctor, service_date))


def queue_actor_kind(actor: Actor, queue: Queue) -> ActorKind | None:
    if actor.can(Perm.QUEUE_MANAGE, queue.hospital_id):
        return "staff"
    if actor.doctor_ids.get(queue.hospital_id) == queue.doctor_id and actor.can(Perm.QUEUE_CONSULT, queue.hospital_id):
        return "doctor"
    return None


def entry_actor_kind(actor: Actor, entry: QueueEntry, queue: Queue) -> ActorKind | None:
    kind = queue_actor_kind(actor, queue)
    if kind:
        return kind
    if actor.patient_id is not None and actor.patient_id == entry.patient_id:
        return "patient"
    return None


def _load_entry_for_change(actor: Actor, entry_id: uuid.UUID, expected_version: int | None,
                           allowed: tuple[ActorKind, ...]) -> tuple[QueueEntry, Queue, ActorKind]:
    """Lock the entry's queue, then the entry, and check access and version."""
    queue_id = db.session.scalar(select(QueueEntry.queue_id).where(QueueEntry.id == entry_id))
    if queue_id is None:
        raise NotFound("Queue entry not found.")
    queue = _lock_queue(queue_id)
    entry = _lock_entry(entry_id)
    kind = entry_actor_kind(actor, entry, queue)
    if kind is None:
        raise NotFound("Queue entry not found.")
    if kind not in allowed:
        raise Forbidden()
    if expected_version is not None and expected_version != entry.version:
        raise Conflict("This queue entry was changed by someone else. Reload and try again.", code="stale_version",
                       details={"current_version": entry.version})
    return entry, queue, kind


def _role_label(actor: Actor | None, kind: str, hospital_id: uuid.UUID) -> str:
    if actor is None:
        return "system"
    if kind == "staff":
        roles = actor.roles_in(hospital_id)
        return "receptionist" if "receptionist" in roles else ("hospital_admin" if "hospital_admin" in roles else "staff")
    return kind


def _event(queue: Queue, entry: QueueEntry | None, event_type: str, *, from_status: str | None, actor: Actor | None,
           kind: str, reason: str | None = None, details: dict | None = None) -> None:
    db.session.add(QueueEvent(
        queue_id=queue.id, entry_id=entry.id if entry else None, hospital_id=queue.hospital_id, event_type=event_type,
        from_status=from_status, to_status=entry.status if entry else None,
        actor_user_id=actor.user_id if actor else None, actor_role=_role_label(actor, kind, queue.hospital_id),
        reason=reason, details=details or {}))
    audit_service.record(f"queue.{event_type}", actor=actor, hospital_id=queue.hospital_id,
                         target_type="queue_entry" if entry else "queue", target_id=entry.id if entry else queue.id,
                         reason=reason, details={"from_status": from_status,
                                                 "to_status": entry.status if entry else None, **(details or {})})
    publish("queue_entry" if entry else "queue", action=event_type, hospital_id=queue.hospital_id, queue_id=queue.id,
            entry_id=entry.id if entry else None, appointment_id=entry.appointment_id if entry else None,
            doctor_id=queue.doctor_id, patient_id=entry.patient_id if entry else None,
            status=entry.status if entry else ("paused" if queue.is_paused else "open"))


def _transition(entry: QueueEntry, event: str, kind: str) -> str:
    from_status = entry.status
    entry.status = check_transition(event, from_status, kind)
    entry.version += 1
    return from_status


def _doctor_name(doctor: DoctorProfile) -> str:
    return f"{doctor.title} {doctor.user.full_name}" if doctor.title else doctor.user.full_name


def _notify(entry: QueueEntry, queue: Queue, kind: str, title: str, body: str,
            sms: bool | None = None, email: bool = True) -> None:
    user_id = entry.patient.user_id if entry.patient else None
    phone = entry.patient.phone if entry.patient else None

    user_email = None
    if entry.patient and entry.patient.user:
        user_email = entry.patient.user.email
    elif user_id:
        user_email = db.session.scalar(select(User.email).where(User.id == user_id))

    # First call: SMS + Email + Push
    # Subsequent calls (recalls): Email + Push only (no repetitive SMS)
    if sms is None:
        if kind == "called":
            sms = (entry.call_count <= 1)
        else:
            sms = True

    notification_service.notify(
        user_id=user_id,
        hospital_id=queue.hospital_id,
        type=f"queue.{kind}",
        title=title,
        body=body,
        related_type="queue_entry",
        related_id=entry.id,
        phone=phone,
        email=user_email,
        sms=sms,
        send_email=email,
    )


def _notify_next_in_line(entry: QueueEntry, queue: Queue, doctor: DoctorProfile | None = None,
                         actor: Actor | None = None) -> None:
    if doctor is None:
        doctor = db.session.get(DoctorProfile, queue.doctor_id)
    already = db.session.scalar(
        select(QueueEvent.id).where(
            QueueEvent.entry_id == entry.id,
            QueueEvent.event_type == "next_in_line",
        )
    )
    if already:
        return

    _event(queue, entry, "next_in_line", from_status=entry.status, actor=actor, kind="system",
           details={"ticket_number": entry.ticket_number})
    doc_name = _doctor_name(doctor) if doctor else "the doctor"
    _notify(entry, queue, "next", "You are next in line",
            f"Ticket {entry.ticket_number}: you are next to see {doc_name}. Please stay near the waiting area.")


def _flush_queue_constraints() -> None:
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
        if constraint == "uq_queue_entries_one_with_doctor":
            raise Conflict("Another patient is already with the doctor.", code="patient_already_called") from None
        if constraint == "uq_queue_entries_active_patient_day":
            raise Conflict("This patient is already in a queue today.", code="already_in_queue") from None
        if constraint == "uq_queue_entries_active_appointment":
            raise Conflict("This appointment is already checked in.", code="already_checked_in") from None
        raise


# --- Joining the queue ---------------------------------------------------------------------


def _new_entry(queue: Queue, *, patient_id: uuid.UUID, kind: str, appointment: Appointment | None,
               method: str, actor: Actor, priority_level: int = 0, priority_reason: str | None = None,
               sort_key=None, checked_in_at=None, arrival_confirmed: bool = False) -> QueueEntry:
    now = utcnow()
    checked_in_at = checked_in_at or now
    if sort_key is None:
        grace = timedelta(minutes=get_setting(_hospital(queue), "checkin_grace_minutes"))
        sort_key = initial_sort_key(kind=kind, checked_in_at=checked_in_at,
                                    appointment_start=appointment.start_at if appointment else None, grace=grace)
    ticket = queue.next_ticket_number
    queue.next_ticket_number += 1
    entry = QueueEntry(
        queue_id=queue.id, hospital_id=queue.hospital_id, service_date=queue.service_date, patient_id=patient_id,
        appointment_id=appointment.id if appointment else None, kind=kind, ticket_number=ticket, status=Q.WAITING,
        priority_level=priority_level, priority_reason=priority_reason, sort_key=sort_key,
        checked_in_at=checked_in_at, check_in_method=method, entered_queue_at=now,
        arrival_confirmed_at=now if arrival_confirmed else None,
        arrival_confirmed_by_user_id=actor.user_id if arrival_confirmed else None,
    )
    db.session.add(entry)
    _flush_queue_constraints()
    return entry


def _hospital(queue: Queue) -> Hospital:
    return db.session.get(Hospital, queue.hospital_id)


def _record_initial_estimate(entry: QueueEntry, queue: Queue) -> None:
    est = compute_estimates(queue, _queue_entries(queue.id)).get(entry.id)
    if est and est.min_minutes is not None:
        entry.initial_estimate_min, entry.initial_estimate_max = est.min_minutes, est.max_minutes


def check_in(actor: Actor, appointment_id: uuid.UUID) -> QueueEntry:
    """Check in an approved appointment and place the patient in the doctor's queue for that day."""
    appt = appointment_service.lock_for_queue(appointment_id)
    kind = appointment_service.actor_kind_for(actor, appt) if appt else None
    if appt is None or kind is None:
        raise NotFound("Appointment not found.")
    if kind == "staff" and not actor.can(Perm.QUEUE_MANAGE, appt.hospital_id):
        raise Forbidden()
    if kind not in ("patient", "staff"):
        raise Forbidden()
    hospital = appt.hospital
    if kind == "patient" and not get_setting(hospital, "allow_patient_self_checkin"):
        raise Forbidden("Please check in at the reception desk.")
    if appt.status != AppointmentStatus.APPROVED:
        check_appointment_transition("check_in", appt.status, kind)  # raises the standard 409

    now = utcnow()
    opens = appt.start_at - timedelta(minutes=get_setting(hospital, "checkin_open_minutes_before"))
    closes = appt.start_at + timedelta(minutes=get_setting(hospital, "checkin_close_minutes_after"))
    if now < opens:
        raise Conflict("Check-in is not open yet for this appointment.", code="checkin_not_open",
                       details={"opens_at": opens.isoformat()})
    if now > closes and kind == "patient":
        raise Conflict("Check-in has closed for this appointment. Please speak to the reception desk.",
                       code="checkin_closed")

    service_date = appt.start_at.astimezone(hospital_tz(hospital)).date()
    queue = _get_or_create_queue(appt.doctor, service_date)
    entry = _new_entry(queue, patient_id=appt.patient_id, kind="appointment", appointment=appt,
                       method="self" if kind == "patient" else "staff", actor=actor,
                       arrival_confirmed=(kind == "staff"))
    appointment_service.apply_queue_transition(appt, "check_in", actor=actor, actor_kind=kind,
                                               actor_role=_role_label(actor, kind, queue.hospital_id),
                                               history_event="checked_in")
    _record_initial_estimate(entry, queue)
    _event(queue, entry, "checked_in", from_status=None, actor=actor, kind=kind,
           details={"kind": "appointment", "method": entry.check_in_method, "ticket_number": entry.ticket_number})
    db.session.commit()
    return entry


def add_walk_in(actor: Actor, hospital_id: uuid.UUID, *, patient_id: uuid.UUID, doctor_id: uuid.UUID) -> QueueEntry:
    hospital = get_hospital(hospital_id)
    if not actor.can(Perm.QUEUE_MANAGE, hospital_id):
        raise Forbidden()
    if not db.session.scalar(select(HospitalPatientRecord.id).where(
            HospitalPatientRecord.hospital_id == hospital_id, HospitalPatientRecord.patient_id == patient_id)):
        raise NotFound("Patient not found.")
    doctor = db.session.get(DoctorProfile, doctor_id)
    if doctor is None or doctor.hospital_id != hospital_id or not doctor.is_active:
        raise NotFound("Doctor not found.")
    queue = _get_or_create_queue(doctor, utcnow().astimezone(hospital_tz(hospital)).date())
    entry = _new_entry(queue, patient_id=patient_id, kind="walk_in", appointment=None, method="staff", actor=actor,
                       arrival_confirmed=True)
    _record_initial_estimate(entry, queue)
    _event(queue, entry, "walk_in_added", from_status=None, actor=actor, kind="staff",
           details={"kind": "walk_in", "ticket_number": entry.ticket_number})
    db.session.commit()
    return entry


# --- Calling and consultations ---------------------------------------------------------------


def call_next(actor: Actor, queue_id: uuid.UUID) -> QueueEntry:
    queue = _lock_queue(queue_id)
    kind = queue_actor_kind(actor, queue) if queue else None
    if queue is None or kind is None:
        raise NotFound("Queue not found.")
    hospital = _hospital(queue)
    doctor = db.session.get(DoctorProfile, queue.doctor_id)
    if queue.service_date != availability.local_today(hospital):
        raise Conflict("Patients can only be called from today's queue.", code="queue_not_today")
    if queue.is_paused:
        raise Conflict("The queue is paused. Resume it to call the next patient.", code="queue_paused")
    if availability.effective_status(doctor) != DoctorStatus.AVAILABLE:
        raise Conflict("The doctor must be marked available to call the next patient.", code="doctor_not_available")

    entries = _queue_entries(queue.id)
    if any(e.status in Q.WITH_DOCTOR for e in entries):
        raise Conflict("Another patient is already with the doctor. Start, complete or mark them missed first.",
                       code="patient_already_called")
    candidates = ordered_waiting(entries)
    if get_setting(hospital, "require_arrival_confirmation"):
        candidates = [e for e in candidates if e.check_in_method != "self" or e.arrival_confirmed_at is not None]
    if not candidates:
        raise Conflict("No eligible patients are waiting.", code="queue_empty")

    entry = _lock_entry(candidates[0].id)
    from_status = _transition(entry, "call", kind)
    entry.called_at = utcnow()
    entry.call_count += 1
    _flush_queue_constraints()
    _event(queue, entry, "called", from_status=from_status, actor=actor, kind=kind,
           details={"ticket_number": entry.ticket_number, "call_count": entry.call_count})
    _notify(entry, queue, "called", "It's your turn",
            f"Ticket {entry.ticket_number}: please go to {_doctor_name(doctor)} now.")
    remaining = [c for c in candidates[1:] if c.status == Q.WAITING]
    if remaining:
        _notify_next_in_line(remaining[0], queue, doctor, actor=actor)
    db.session.commit()
    return entry


def recall(actor: Actor, entry_id: uuid.UUID, *, expected_version: int | None = None) -> QueueEntry:
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff", "doctor"))
    from_status = _transition(entry, "recall", kind)
    entry.call_count += 1
    entry.called_at = utcnow()
    _event(queue, entry, "recalled", from_status=from_status, actor=actor, kind=kind,
           details={"call_count": entry.call_count})
    _notify(entry, queue, "called", "You are being called again",
            f"Ticket {entry.ticket_number}: please go to the consultation room now.")
    db.session.commit()
    return entry


def start(actor: Actor, entry_id: uuid.UUID, *, expected_version: int | None = None) -> QueueEntry:
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff", "doctor"))
    from_status = _transition(entry, "start", kind)
    entry.started_at = utcnow()
    db.session.add(ConsultationSession(entry_id=entry.id, doctor_id=queue.doctor_id, hospital_id=queue.hospital_id,
                                       started_at=entry.started_at))
    _event(queue, entry, "consultation_started", from_status=from_status, actor=actor, kind=kind)
    db.session.commit()
    return entry


def complete(actor: Actor, entry_id: uuid.UUID, *, expected_version: int | None = None) -> QueueEntry:
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff", "doctor"))
    from_status = _transition(entry, "complete", kind)
    now = utcnow()
    entry.completed_at = now
    session = db.session.scalar(select(ConsultationSession).where(ConsultationSession.entry_id == entry.id))
    session.ended_at = now
    session.duration_seconds = int((now - session.started_at).total_seconds())
    if entry.appointment_id:
        appt = appointment_service.lock_for_queue(entry.appointment_id)
        appointment_service.apply_queue_transition(appt, "complete", actor=actor, actor_kind="staff",
                                                   actor_role=_role_label(actor, kind, queue.hospital_id),
                                                   history_event="completed")
    _event(queue, entry, "consultation_completed", from_status=from_status, actor=actor, kind=kind,
           details={"duration_seconds": session.duration_seconds})
    waiting = ordered_waiting(_queue_entries(queue.id))
    if waiting:
        doctor = db.session.get(DoctorProfile, queue.doctor_id)
        _notify_next_in_line(waiting[0], queue, doctor, actor=actor)
    db.session.commit()
    return entry


def mark_missed(actor: Actor, entry_id: uuid.UUID, *, reason: str | None,
                expected_version: int | None = None) -> QueueEntry:
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff", "doctor"))
    from_status = _transition(entry, "mark_missed", kind)
    entry.missed_count += 1
    _event(queue, entry, "missed", from_status=from_status, actor=actor, kind=kind, reason=reason,
           details={"missed_count": entry.missed_count})
    _notify(entry, queue, "missed", "You missed your call",
            "You were called but did not respond. Please speak to the reception desk to rejoin the queue.")
    db.session.commit()
    return entry


def return_to_queue(actor: Actor, entry_id: uuid.UUID, *, reason: str | None,
                    expected_version: int | None = None) -> QueueEntry:
    """A patient who missed their call comes back: they rejoin at the back of the queue."""
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff",))
    if entry.status == Q.MISSED and entry.missed_count >= get_setting(_hospital(queue), "max_missed_calls"):
        raise Conflict("This patient has missed too many calls to rejoin automatically. Register them again "
                       "as a walk-in if appropriate.", code="too_many_missed_calls")
    from_status = _transition(entry, "return", kind)
    entry.sort_key = utcnow()
    _event(queue, entry, "returned", from_status=from_status, actor=actor, kind=kind, reason=reason)
    _notify(entry, queue, "returned", "Back in the queue", f"Ticket {entry.ticket_number} has rejoined the queue.")
    db.session.commit()
    return entry


def cancel(actor: Actor, entry_id: uuid.UUID, *, reason: str | None,
           expected_version: int | None = None) -> QueueEntry:
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff", "patient"))
    if kind == "staff" and not (reason and reason.strip()):
        raise ValidationFailed(details={"reason": ["A reason is required."]})
    from_status = _transition(entry, "cancel", kind)
    entry.end_reason = reason
    if entry.appointment_id:
        appt = appointment_service.lock_for_queue(entry.appointment_id)
        appointment_service.apply_queue_transition(appt, "cancel_checked_in", actor=actor,
                                                   actor_kind="patient" if kind == "patient" else "staff",
                                                   actor_role=_role_label(actor, kind, queue.hospital_id),
                                                   history_event="cancelled_after_check_in", reason=reason)
    _event(queue, entry, "cancelled", from_status=from_status, actor=actor, kind=kind, reason=reason)
    if kind == "staff":
        _notify(entry, queue, "cancelled", "Removed from the queue",
                f"Ticket {entry.ticket_number} was removed from the queue by the hospital.")
    waiting = ordered_waiting(_queue_entries(queue.id))
    if waiting:
        doctor = db.session.get(DoctorProfile, queue.doctor_id)
        _notify_next_in_line(waiting[0], queue, doctor, actor=actor)
    db.session.commit()
    return entry


def transfer(actor: Actor, entry_id: uuid.UUID, *, doctor_id: uuid.UUID, reason: str,
             expected_version: int | None = None) -> QueueEntry:
    """Move a waiting patient to another doctor's queue the same day, keeping their place in line."""
    queue_id = db.session.scalar(select(QueueEntry.queue_id).where(QueueEntry.id == entry_id))
    if queue_id is None:
        raise NotFound("Queue entry not found.")
    source_preview = db.session.get(Queue, queue_id)
    target_doctor = db.session.get(DoctorProfile, doctor_id)
    if target_doctor is None or target_doctor.hospital_id != source_preview.hospital_id or not target_doctor.is_active:
        raise NotFound("Doctor not found.")
    if target_doctor.id == source_preview.doctor_id:
        raise ValidationFailed(details={"doctor_id": ["Choose a different doctor."]})

    # Lock both queues in a consistent order so two opposite transfers cannot deadlock.
    target_queue_id = _ensure_queue_id(target_doctor, source_preview.service_date)
    for qid in sorted([queue_id, target_queue_id], key=str):
        _lock_queue(qid)
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff",))
    target_queue = db.session.get(Queue, target_queue_id)

    from_status = _transition(entry, "transfer", kind)
    entry.end_reason = reason
    _flush_queue_constraints()  # free the patient's "one live place" before creating the new entry
    new_entry = _new_entry(target_queue, patient_id=entry.patient_id, kind=entry.kind,
                           appointment=db.session.get(Appointment, entry.appointment_id) if entry.appointment_id else None,
                           method=entry.check_in_method, actor=actor, priority_level=entry.priority_level,
                           priority_reason=entry.priority_reason, sort_key=entry.sort_key,
                           checked_in_at=entry.checked_in_at, arrival_confirmed=entry.arrival_confirmed_at is not None)
    entry.transferred_to_entry_id = new_entry.id
    _event(queue, entry, "transferred_out", from_status=from_status, actor=actor, kind=kind, reason=reason,
           details={"to_entry_id": str(new_entry.id), "to_doctor_id": str(target_doctor.id)})
    _event(target_queue, new_entry, "transferred_in", from_status=None, actor=actor, kind=kind, reason=reason,
           details={"from_entry_id": str(entry.id), "ticket_number": new_entry.ticket_number})
    _notify(new_entry, target_queue, "transferred", "Moved to another doctor",
            f"You are now waiting for {_doctor_name(target_doctor)}. Your new ticket is {new_entry.ticket_number}.")
    db.session.commit()
    return new_entry


def set_priority(actor: Actor, entry_id: uuid.UUID, *, level: int, reason: str,
                 expected_version: int | None = None) -> QueueEntry:
    """Authorised staff change a waiting patient's priority. Clinical triage stays with qualified staff."""
    entry, queue, kind = _load_entry_for_change(actor, entry_id, expected_version, ("staff",))
    if not actor.can(Perm.QUEUE_PRIORITY_SET, queue.hospital_id):
        raise Forbidden("You are not authorised to change queue priority.")
    if not (reason and reason.strip()):
        raise ValidationFailed(details={"reason": ["A reason is required."]})
    from_status = _transition(entry, "set_priority", kind)
    previous = entry.priority_level
    entry.priority_level = level
    entry.priority_reason = reason if level > 0 else None
    entry.priority_set_by_user_id = actor.user_id
    _event(queue, entry, "priority_changed", from_status=from_status, actor=actor, kind=kind, reason=reason,
           details={"from_level": previous, "to_level": level})
    db.session.commit()
    return entry


def confirm_arrival(actor: Actor, entry_id: uuid.UUID) -> QueueEntry:
    entry, queue, kind = _load_entry_for_change(actor, entry_id, None, ("staff",))
    check_transition("confirm_arrival", entry.status, "staff")
    if entry.arrival_confirmed_at is None:
        entry.arrival_confirmed_at = utcnow()
        entry.arrival_confirmed_by_user_id = actor.user_id
        entry.version += 1
        _event(queue, entry, "arrival_confirmed", from_status=entry.status, actor=actor, kind=kind)
        db.session.commit()
    return entry


def set_paused(actor: Actor, queue_id: uuid.UUID, *, paused: bool, reason: str | None) -> Queue:
    queue = _lock_queue(queue_id)
    kind = queue_actor_kind(actor, queue) if queue else None
    if queue is None or kind is None:
        raise NotFound("Queue not found.")
    if queue.is_paused == paused:
        raise Conflict(f"The queue is already {'paused' if paused else 'running'}.", code="queue_state_unchanged")
    queue.is_paused = paused
    queue.paused_reason = reason if paused else None
    _event(queue, None, "paused" if paused else "resumed", from_status=None, actor=actor, kind=kind, reason=reason)
    db.session.commit()
    return queue


# --- End of day ------------------------------------------------------------------------------


def close_past_days() -> int:
    """Close queues from earlier days: unseen patients are cancelled (appointments become no-shows);
    consultations left open are closed without a duration so they don't skew estimates."""
    closed = 0
    for hospital in db.session.scalars(select(Hospital)).all():
        today = availability.local_today(hospital)
        queue_ids = db.session.scalars(select(Queue.id).where(Queue.hospital_id == hospital.id,
                                                              Queue.service_date < today)).all()
        for queue_id in queue_ids:
            queue = _lock_queue(queue_id)
            for entry in [e for e in _queue_entries(queue.id) if e.status in Q.ACTIVE]:
                entry = _lock_entry(entry.id)
                from_status = entry.status
                if from_status == Q.IN_CONSULTATION:
                    entry.status = check_transition("close_day_in_consultation", from_status, "system")
                    entry.completed_at = utcnow()
                    session = db.session.scalar(select(ConsultationSession).where(ConsultationSession.entry_id == entry.id))
                    session.ended_at = utcnow()
                    appt_event, history_event = "complete", "completed"
                else:
                    entry.status = check_transition("close_day_unseen", from_status, "system")
                    entry.end_reason = "Queue closed at end of day"
                    appt_event, history_event = "close_unseen", "closed_unseen"
                entry.version += 1
                if entry.appointment_id:
                    appt = appointment_service.lock_for_queue(entry.appointment_id)
                    appointment_service.apply_queue_transition(appt, appt_event, actor=None, actor_kind="system",
                                                               actor_role="system", history_event=history_event)
                _event(queue, entry, "closed_end_of_day", from_status=from_status, actor=None, kind="system")
                closed += 1
            db.session.commit()
    return closed


# --- Queries and estimates ---------------------------------------------------------------------


def _queue_entries(queue_id: uuid.UUID) -> list[QueueEntry]:
    return list(db.session.scalars(select(QueueEntry).where(QueueEntry.queue_id == queue_id)
                                   .order_by(QueueEntry.ticket_number)))


def _durations(doctor_id: uuid.UUID, hospital: Hospital) -> list[float]:
    since = utcnow() - timedelta(days=get_setting(hospital, "estimate_history_days"))
    rows = db.session.scalars(select(ConsultationSession.duration_seconds).where(
        ConsultationSession.doctor_id == doctor_id, ConsultationSession.ended_at >= since,
        ConsultationSession.duration_seconds.is_not(None))
        .order_by(ConsultationSession.ended_at.desc()).limit(500)).all()
    return [s / 60 for s in rows]


def compute_estimates(queue: Queue, entries: list[QueueEntry]) -> dict[uuid.UUID, Estimate]:
    hospital = _hospital(queue)
    doctor = db.session.get(DoctorProfile, queue.doctor_id)
    now = utcnow()
    stats = duration_stats(_durations(doctor.id, hospital), fallback_minutes=doctor.default_consult_minutes,
                           min_samples=get_setting(hospital, "estimate_min_samples"))
    with_doctor = next((e for e in entries if e.status in Q.WITH_DOCTOR), None)
    waiting = ordered_waiting(entries)
    common = dict(now=now, doctor_status=availability.effective_status(doctor, now),
                  expected_return_at=availability.effective_expected_return(doctor, now),
                  next_session_start=availability.next_session_start(doctor, now), queue_paused=queue.is_paused,
                  with_doctor_status=with_doctor.status if with_doctor else None,
                  with_doctor_started_at=with_doctor.started_at if with_doctor else None, stats=stats)
    result = {}
    position = {e.id: i for i, e in enumerate(waiting)}
    for e in entries:
        result[e.id] = estimate(EstimateInputs(entry_status=e.status, patients_ahead=position.get(e.id, 0), **common))
    return result


@dataclass
class EntryView:
    entry: QueueEntry
    queue: Queue
    position: int | None
    estimate: Estimate
    kind: ActorKind


def _views(queue: Queue, entries: list[QueueEntry], kind: ActorKind, only: set[uuid.UUID] | None = None) -> list[EntryView]:
    estimates = compute_estimates(queue, entries)
    positions = {e.id: i + 1 for i, e in enumerate(ordered_waiting(entries))}
    return [EntryView(e, queue, positions.get(e.id), estimates[e.id], kind)
            for e in entries if only is None or e.id in only]


def get_queue(actor: Actor, queue_id: uuid.UUID) -> tuple[Queue, ActorKind]:
    queue = db.session.get(Queue, queue_id)
    kind = queue_actor_kind(actor, queue) if queue else None
    if queue is None or kind is None:
        raise NotFound("Queue not found.")
    return queue, kind


def list_entries(actor: Actor, queue_id: uuid.UUID, *, include_finished: bool) -> list[EntryView]:
    queue, kind = get_queue(actor, queue_id)
    entries = _queue_entries(queue.id)
    views = _views(queue, entries, kind)
    if not include_finished:
        views = [v for v in views if v.entry.status in Q.ACTIVE]
    status_rank = {Q.IN_CONSULTATION: 0, Q.CALLED: 1, Q.WAITING: 2, Q.MISSED: 3}
    return sorted(views, key=lambda v: (status_rank.get(v.entry.status, 4), v.position or 0, v.entry.ticket_number))


def get_entry(actor: Actor, entry_id: uuid.UUID) -> EntryView:
    entry = db.session.get(QueueEntry, entry_id)
    queue = db.session.get(Queue, entry.queue_id) if entry else None
    kind = entry_actor_kind(actor, entry, queue) if entry else None
    if entry is None or kind is None:
        raise NotFound("Queue entry not found.")
    return _views(queue, _queue_entries(queue.id), kind, only={entry.id})[0]


def my_active_entries(actor: Actor) -> list[EntryView]:
    if actor.patient_id is None:
        raise Forbidden("This action is only available to patients.")
    entries = db.session.scalars(select(QueueEntry).where(QueueEntry.patient_id == actor.patient_id,
                                                          QueueEntry.status.in_(Q.ACTIVE))).all()
    views = []
    for e in entries:
        queue = db.session.get(Queue, e.queue_id)
        views += _views(queue, _queue_entries(queue.id), "patient", only={e.id})
    return views


def entry_events(actor: Actor, entry_id: uuid.UUID) -> tuple[list[QueueEvent], ActorKind]:
    view = get_entry(actor, entry_id)
    events = db.session.scalars(select(QueueEvent).where(QueueEvent.entry_id == entry_id)
                                .order_by(QueueEvent.occurred_at, QueueEvent.id)).all()
    return list(events), view.kind


def queue_events(actor: Actor, queue_id: uuid.UUID, limit: int = 200) -> list[QueueEvent]:
    get_queue(actor, queue_id)
    return list(db.session.scalars(select(QueueEvent).where(QueueEvent.queue_id == queue_id)
                                   .order_by(QueueEvent.occurred_at.desc(), QueueEvent.id.desc()).limit(limit)))


@dataclass
class QueueSummary:
    queue: Queue
    doctor: DoctorProfile
    counts: dict[str, int]


def list_queues(actor: Actor, hospital_id: uuid.UUID, *, service_date: date | None, doctor_id: uuid.UUID | None,
                department_id: uuid.UUID | None) -> list[QueueSummary]:
    hospital = get_hospital(hospital_id)
    if actor.can(Perm.QUEUE_MANAGE, hospital_id):
        pass
    elif hospital_id in actor.doctor_ids:
        if doctor_id is not None and doctor_id != actor.doctor_ids[hospital_id]:
            raise Forbidden("Doctors can only view their own queue.")
        doctor_id = actor.doctor_ids[hospital_id]
    else:
        raise Forbidden()
    service_date = service_date or availability.local_today(hospital)
    stmt = select(Queue).where(Queue.hospital_id == hospital_id, Queue.service_date == service_date)
    if doctor_id:
        stmt = stmt.where(Queue.doctor_id == doctor_id)
    if department_id:
        stmt = stmt.where(Queue.department_id == department_id)
    queues = db.session.scalars(stmt).all()
    counts_rows = db.session.execute(
        select(QueueEntry.queue_id, QueueEntry.status, func.count()).where(QueueEntry.queue_id.in_([q.id for q in queues]))
        .group_by(QueueEntry.queue_id, QueueEntry.status)).all() if queues else []
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for qid, status, n in counts_rows:
        counts.setdefault(qid, {})[status] = n
    return [QueueSummary(q, db.session.get(DoctorProfile, q.doctor_id), counts.get(q.id, {})) for q in queues]
