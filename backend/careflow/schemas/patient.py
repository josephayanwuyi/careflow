from marshmallow import Schema, fields, validate

from careflow.schemas.common import name_field, phone_field

SEX_CHOICES = ["female", "male", "other", "undisclosed"]


class MyProfileSchema(Schema):
    id = fields.UUID(dump_only=True)
    full_name = name_field()
    phone = phone_field(allow_none=True)
    date_of_birth = fields.Date(allow_none=True)
    sex = fields.Str(allow_none=True, validate=validate.OneOf(SEX_CHOICES))


class MyProfileUpdateSchema(MyProfileSchema):
    """PATCH body: every field is optional (none are required on the base schema)."""


class WalkInRegistrationSchema(Schema):
    full_name = name_field(required=True)
    phone = phone_field(load_default=None, allow_none=True)
    date_of_birth = fields.Date(load_default=None, allow_none=True)
    sex = fields.Str(load_default=None, allow_none=True, validate=validate.OneOf(SEX_CHOICES))
    confirm_not_duplicate = fields.Bool(load_default=False)
    duplicate_override_reason = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))


class PatientSearchArgs(Schema):
    q = fields.Str(required=True, validate=validate.Length(min=3, max=100))


class HospitalPatientSchema(Schema):
    """A patient as seen by staff of one hospital."""

    id = fields.UUID()
    hospital_patient_number = fields.Str()
    full_name = fields.Str()
    phone = fields.Str(allow_none=True)
    date_of_birth = fields.Date(allow_none=True)
    sex = fields.Str(allow_none=True)
    has_online_account = fields.Bool()
    registered_at = fields.DateTime()


def hospital_patient_view(patient, record) -> dict:
    return {
        "id": patient.id,
        "hospital_patient_number": record.hospital_patient_number,
        "full_name": patient.full_name,
        "phone": patient.phone,
        "date_of_birth": patient.date_of_birth,
        "sex": patient.sex,
        "has_online_account": patient.user_id is not None,
        "registered_at": record.created_at,
    }
