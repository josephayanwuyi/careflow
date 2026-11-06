from marshmallow import Schema, fields, validate

from careflow.models import Appointment, DoctorProfile, HospitalPatientRecord
from careflow.extensions import db
from careflow.services import availability_service as availability
from careflow.services.queue_rules import allowed_events

_version = fields.Int(load_default=None, validate=validate.Range(min=1))


class WalkInEntrySchema(Schema):
    patient_id = fields.UUID(required=True)
    doctor_id = fields.UUID(required=True)


class VersionSchema(Schema):
    expected_version = _version


class OptionalReasonSchema(Schema):
    reason = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=300))
    expected_version = _version


class TransferSchema(Schema):
    doctor_id = fields.UUID(required=True)
    reason = fields.Str(required=True, validate=validate.Length(min=3, max=300))
    expected_version = _version


class PrioritySchema(Schema):
    level = fields.Int(required=True, validate=validate.Range(min=0, max=3),
                       metadata={"description": "0 = normal. Set only by authorised staff; never computed."})
    reason = fields.Str(required=True, validate=validate.Length(min=3, max=300))
    expected_version = _version


class PauseSchema(Schema):
    reason = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=300))


class QueueListArgs(Schema):
    date = fields.Date(load_default=None, metadata={"description": "Hospital local date; defaults to today"})
    doctor_id = fields.UUID(load_default=None)
    department_id = fields.UUID(load_default=None)


class EntryListArgs(Schema):
    include_finished = fields.Bool(load_default=False)


class EstimateSchema(Schema):
    state = fields.Str(metadata={"description": "estimated | next | called | being_seen | unavailable | not_waiting"})
    min_minutes = fields.Int(allow_none=True)
    max_minutes = fields.Int(allow_none=True)
    provisional = fields.Bool()
    explanation = fields.Str()
    basis = fields.Dict(metadata={"description": "Inputs used (staff and doctors only)"})


class DoctorBriefSchema(Schema):
    id = fields.UUID()
    name = fields.Str()
    public_status = fields.Str()


class PatientBriefSchema(Schema):
    id = fields.UUID()
    full_name = fields.Str()
    hospital_patient_number = fields.Str(allow_none=True)


class QueueEntrySchema(Schema):
    id = fields.UUID()
    queue_id = fields.UUID()
    service_date = fields.Date()
    hospital_timezone = fields.Str()
    ticket_number = fields.Int()
    status = fields.Str()
    position = fields.Int(allow_none=True, metadata={"description": "1 = next among waiting patients"})
    queue_paused = fields.Bool()
    checked_in_at = fields.DateTime()
    estimate = fields.Nested(EstimateSchema)
    doctor = fields.Nested(DoctorBriefSchema)
    allowed_actions = fields.List(fields.Str())
    version = fields.Int()
    # Staff and doctor view only
    patient = fields.Nested(PatientBriefSchema, allow_none=True)
    kind = fields.Str(allow_none=True)
    appointment_id = fields.UUID(allow_none=True)
    appointment_start_at = fields.DateTime(allow_none=True)
    reason_for_visit = fields.Str(allow_none=True)
    priority_level = fields.Int(allow_none=True)
    priority_reason = fields.Str(allow_none=True)
    check_in_method = fields.Str(allow_none=True)
    arrival_confirmed_at = fields.DateTime(allow_none=True)
    called_at = fields.DateTime(allow_none=True)
    call_count = fields.Int(allow_none=True)
    missed_count = fields.Int(allow_none=True)
    started_at = fields.DateTime(allow_none=True)
    completed_at = fields.DateTime(allow_none=True)


class QueueSummarySchema(Schema):
    id = fields.UUID()
    service_date = fields.Date()
    department_id = fields.UUID()
    doctor = fields.Nested(DoctorBriefSchema)
    doctor_status = fields.Str()
    is_paused = fields.Bool()
    paused_reason = fields.Str(allow_none=True)
    counts = fields.Dict(keys=fields.Str(), values=fields.Int())


class QueueEventSchema(Schema):
    id = fields.UUID()
    entry_id = fields.UUID(allow_none=True)
    event_type = fields.Str()
    from_status = fields.Str(allow_none=True)
    to_status = fields.Str(allow_none=True)
    actor_role = fields.Str(allow_none=True)
    actor_user_id = fields.UUID(allow_none=True)
    reason = fields.Str(allow_none=True)
    details = fields.Dict()
    occurred_at = fields.DateTime()


_PATIENT_ACTIONS = {"cancel"}


def doctor_brief(doctor: DoctorProfile) -> dict:
    name = f"{doctor.title} {doctor.user.full_name}" if doctor.title else doctor.user.full_name
    return {"id": doctor.id, "name": name, "public_status": availability.public_status(doctor)}


def entry_view(view) -> dict:
    e, queue, kind = view.entry, view.queue, view.kind
    doctor = db.session.get(DoctorProfile, queue.doctor_id)
    est = view.estimate
    actions = allowed_events(e.status, kind) if kind in ("staff", "doctor", "patient") else []
    if kind == "patient":
        actions = [a for a in actions if a in _PATIENT_ACTIONS]
    data = {
        "id": e.id, "queue_id": queue.id, "service_date": queue.service_date,
        "hospital_timezone": doctor.hospital.timezone, "ticket_number": e.ticket_number, "status": e.status,
        "position": view.position, "queue_paused": queue.is_paused, "checked_in_at": e.checked_in_at,
        "estimate": {"state": est.state, "min_minutes": est.min_minutes, "max_minutes": est.max_minutes,
                     "provisional": est.provisional, "explanation": est.explanation,
                     "basis": est.basis if kind != "patient" else {}},
        "doctor": doctor_brief(doctor), "allowed_actions": actions, "version": e.version, "patient": None,
    }
    if kind in ("staff", "doctor"):
        appt = db.session.get(Appointment, e.appointment_id) if e.appointment_id else None
        number = db.session.scalar(db.select(HospitalPatientRecord.hospital_patient_number).where(
            HospitalPatientRecord.hospital_id == e.hospital_id, HospitalPatientRecord.patient_id == e.patient_id))
        data.update({
            "patient": {"id": e.patient_id, "full_name": e.patient.full_name, "hospital_patient_number": number},
            "kind": e.kind, "appointment_id": e.appointment_id,
            "appointment_start_at": appt.start_at if appt else None,
            "reason_for_visit": appt.reason_for_visit if appt else None,
            "priority_level": e.priority_level, "priority_reason": e.priority_reason,
            "check_in_method": e.check_in_method, "arrival_confirmed_at": e.arrival_confirmed_at,
            "called_at": e.called_at, "call_count": e.call_count, "missed_count": e.missed_count,
            "started_at": e.started_at, "completed_at": e.completed_at,
        })
    return data


def queue_summary_view(summary) -> dict:
    q = summary.queue
    return {"id": q.id, "service_date": q.service_date, "department_id": q.department_id,
            "doctor": doctor_brief(summary.doctor), "doctor_status": availability.effective_status(summary.doctor),
            "is_paused": q.is_paused, "paused_reason": q.paused_reason, "counts": summary.counts}


def queue_event_view(event, kind: str) -> dict:
    return {"id": event.id, "entry_id": event.entry_id, "event_type": event.event_type,
            "from_status": event.from_status, "to_status": event.to_status, "actor_role": event.actor_role,
            "actor_user_id": event.actor_user_id if kind != "patient" else None,
            "reason": event.reason if kind != "patient" else None, "details": event.details if kind != "patient" else {},
            "occurred_at": event.occurred_at}
