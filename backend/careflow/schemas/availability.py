from marshmallow import Schema, fields, validate

from careflow.models import DoctorStatus
from careflow.services import availability_service as availability

SELF_SETTABLE = [s for s in DoctorStatus.ALL if s != DoctorStatus.OFF_DUTY]


class OwnStatusSchema(Schema):
    status = fields.Str(required=True, validate=validate.OneOf(SELF_SETTABLE),
                        metadata={"description": "checked_in = arrived; checked_out = left for the day"})
    hospital_id = fields.UUID(load_default=None, metadata={"description": "Needed only if you work at several hospitals"})
    note = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=300))
    expected_return_at = fields.AwareDateTime(load_default=None, allow_none=True)


class OwnStatusQuery(Schema):
    hospital_id = fields.UUID(load_default=None)


class VerifyArrivalSchema(Schema):
    note = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=300))


class DoctorAvailabilitySchema(Schema):
    doctor_id = fields.UUID()
    name = fields.Str()
    department_id = fields.UUID()
    department_name = fields.Str()
    status = fields.Str(metadata={"description": "Effective status (resets to off_duty each day)"})
    public_status = fields.Str()
    status_source = fields.Str(allow_none=True, metadata={"description": "self_reported | staff_verified"})
    status_updated_at = fields.DateTime(allow_none=True)
    expected_return_at = fields.DateTime(allow_none=True)
    note = fields.Str(allow_none=True)
    arrival_verified_today = fields.Bool()
    arrival_verified_at = fields.DateTime(allow_none=True)


class PublicAvailabilitySchema(Schema):
    doctor_id = fields.UUID()
    public_status = fields.Str(metadata={
        "description": "expected | checked_in | available | consultation_in_progress | on_break | "
                       "temporarily_unavailable | unavailable"})


class StatusEventSchema(Schema):
    status = fields.Str()
    source = fields.Str()
    note = fields.Str(allow_none=True)
    expected_return_at = fields.DateTime(allow_none=True)
    actor_user_id = fields.UUID(allow_none=True)
    occurred_at = fields.DateTime()


def availability_view(doctor) -> dict:
    status = availability.effective_status(doctor)
    same_day = status != DoctorStatus.OFF_DUTY or doctor.current_status == DoctorStatus.OFF_DUTY
    return {
        "doctor_id": doctor.id,
        "name": f"{doctor.title} {doctor.user.full_name}" if doctor.title else doctor.user.full_name,
        "department_id": doctor.department_id, "department_name": doctor.department.name,
        "status": status, "public_status": availability.public_status(doctor),
        "status_source": doctor.current_status_source if same_day else None,
        "status_updated_at": doctor.current_status_at if same_day else None,
        "expected_return_at": availability.effective_expected_return(doctor),
        "note": doctor.status_note if same_day else None,
        "arrival_verified_today": availability.arrival_verified_today(doctor),
        "arrival_verified_at": doctor.arrival_verified_at,
    }
