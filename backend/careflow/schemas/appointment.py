from marshmallow import Schema, fields, validate
from webargs.fields import DelimitedList

from careflow.models import AppointmentStatus
from careflow.schemas.common import PaginationArgs
from careflow.services.appointment_rules import allowed_events

_reason = fields.Str(validate=validate.Length(max=500))
_version = fields.Int(load_default=None, validate=validate.Range(min=1),
                      metadata={"description": "Version you last saw; a mismatch returns 409 stale_version"})


class AppointmentRequestSchema(Schema):
    doctor_id = fields.UUID(required=True)
    department_id = fields.UUID(load_default=None)
    start_at = fields.AwareDateTime(required=True, metadata={"description": "Slot start, with timezone offset"})
    reason_for_visit = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=300))


class StaffBookingSchema(AppointmentRequestSchema):
    patient_id = fields.UUID(required=True)


class VersionOnlySchema(Schema):
    expected_version = _version


class ReasonRequiredSchema(Schema):
    reason = fields.Str(required=True, validate=validate.Length(min=3, max=500))
    expected_version = _version


class ReasonOptionalSchema(Schema):
    reason = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))
    expected_version = _version


class ProposeRescheduleSchema(Schema):
    start_at = fields.AwareDateTime(required=True)
    reason = fields.Str(required=True, validate=validate.Length(min=3, max=500))
    expected_version = _version


class MyAppointmentsArgs(PaginationArgs):
    status = DelimitedList(fields.Str(validate=validate.OneOf(AppointmentStatus.ALL)), load_default=list,
                           metadata={"description": "Comma-separated statuses"})


class HospitalAppointmentsArgs(MyAppointmentsArgs):
    doctor_id = fields.UUID(load_default=None)
    department_id = fields.UUID(load_default=None)
    patient_id = fields.UUID(load_default=None)
    date_from = fields.Date(load_default=None, metadata={"description": "Hospital local date, inclusive"})
    date_to = fields.Date(load_default=None, metadata={"description": "Hospital local date, inclusive"})


class PatientSummarySchema(Schema):
    id = fields.UUID()
    full_name = fields.Str()
    hospital_patient_number = fields.Str(allow_none=True)


class AppointmentSchema(Schema):
    id = fields.UUID()
    status = fields.Str()
    hospital_id = fields.UUID()
    hospital_name = fields.Str()
    hospital_timezone = fields.Str()
    department_id = fields.UUID()
    department_name = fields.Str()
    doctor_id = fields.UUID()
    doctor_name = fields.Str()
    start_at = fields.DateTime()
    end_at = fields.DateTime()
    proposed_start_at = fields.DateTime(allow_none=True)
    proposed_end_at = fields.DateTime(allow_none=True)
    status_reason = fields.Str(allow_none=True)
    reason_for_visit = fields.Str(allow_none=True)
    source = fields.Str()
    version = fields.Int()
    created_at = fields.DateTime()
    allowed_actions = fields.List(fields.Str(), metadata={"description": "Actions the caller may take now"})
    patient = fields.Nested(PatientSummarySchema, allow_none=True,
                            metadata={"description": "Only included for hospital staff and the assigned doctor"})


class AppointmentPageSchema(Schema):
    items = fields.List(fields.Nested(AppointmentSchema))
    next_cursor = fields.Str(allow_none=True)


class HistoryEntrySchema(Schema):
    event = fields.Str()
    from_status = fields.Str(allow_none=True)
    to_status = fields.Str()
    actor_role = fields.Str(allow_none=True)
    actor_user_id = fields.UUID(allow_none=True, metadata={"description": "Staff view only"})
    reason = fields.Str(allow_none=True)
    details = fields.Dict()
    occurred_at = fields.DateTime()


# Rule events implemented as API actions in this phase, mapped to the action name clients use.
_EVENT_ACTIONS = {
    "approve": "approve",
    "reject": "reject",
    "propose_reschedule": "propose_reschedule",
    "accept_reschedule": "accept_reschedule",
    "decline_reschedule": "decline_reschedule",
    "cancel_by_patient": "cancel",
    "cancel_by_staff": "cancel",
    "mark_no_show": "mark_no_show",
    "check_in": "check_in",
}


def appointment_view(appt, kind: str, patient_numbers: dict | None = None) -> dict:
    doctor = appt.doctor
    rule_actor = {"patient": "patient", "staff": "staff"}.get(kind)
    actions = sorted({_EVENT_ACTIONS[e] for e in allowed_events(appt.status, rule_actor) if e in _EVENT_ACTIONS}) \
        if rule_actor else []
    view = {
        "id": appt.id,
        "status": appt.status,
        "hospital_id": appt.hospital_id,
        "hospital_name": appt.hospital.name,
        "hospital_timezone": appt.hospital.timezone,
        "department_id": appt.department_id,
        "department_name": appt.department.name,
        "doctor_id": appt.doctor_id,
        "doctor_name": f"{doctor.title} {doctor.user.full_name}" if doctor.title else doctor.user.full_name,
        "start_at": appt.start_at,
        "end_at": appt.end_at,
        "proposed_start_at": appt.proposed_start_at,
        "proposed_end_at": appt.proposed_end_at,
        "status_reason": appt.status_reason,
        "reason_for_visit": appt.reason_for_visit,
        "source": appt.source,
        "version": appt.version,
        "created_at": appt.created_at,
        "allowed_actions": actions,
        "patient": None,
    }
    if kind in ("staff", "doctor"):
        numbers = patient_numbers or {}
        view["patient"] = {
            "id": appt.patient_id,
            "full_name": appt.patient.full_name,
            "hospital_patient_number": numbers.get((appt.hospital_id, appt.patient_id)),
        }
    return view


def history_view(entry, kind: str) -> dict:
    return {
        "event": entry.event,
        "from_status": entry.from_status,
        "to_status": entry.to_status,
        "actor_role": entry.actor_role,
        "actor_user_id": entry.actor_user_id if kind in ("staff", "doctor") else None,
        "reason": entry.reason,
        "details": entry.details,
        "occurred_at": entry.occurred_at,
    }
