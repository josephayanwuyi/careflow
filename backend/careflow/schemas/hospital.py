from marshmallow import Schema, fields, validate

from careflow.models import STAFF_ROLES
from careflow.schemas.common import name_field, password_field, phone_field


class HospitalSchema(Schema):
    id = fields.UUID(dump_only=True)
    code = fields.Str(dump_only=True)
    name = fields.Str(dump_only=True)
    slug = fields.Str(dump_only=True)
    timezone = fields.Str(dump_only=True)
    address = fields.Str(dump_only=True, allow_none=True)


class DepartmentSchema(Schema):
    id = fields.UUID(dump_only=True)
    hospital_id = fields.UUID(dump_only=True)
    name = fields.Str(required=True, validate=validate.Length(min=2, max=120))
    description = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=1000))


class DoctorQuerySchema(Schema):
    department_id = fields.UUID(load_default=None)


class DoctorPublicSchema(Schema):
    """Public doctor listing: no contact details or personal data beyond a display name."""

    id = fields.UUID()
    hospital_id = fields.UUID()
    department_id = fields.UUID()
    department_name = fields.Function(lambda d: d.department.name)
    display_name = fields.Function(lambda d: f"{d.title} {d.user.full_name}" if d.title else d.user.full_name)
    title = fields.Str(allow_none=True)
    specialty = fields.Str(allow_none=True)
    is_accepting_appointments = fields.Bool()


class StaffCreateSchema(Schema):
    email = fields.Email(required=True, validate=validate.Length(max=254))
    full_name = name_field(required=True)
    phone = phone_field(load_default=None, allow_none=True)
    roles = fields.List(fields.Str(validate=validate.OneOf(STAFF_ROLES)), required=True,
                        validate=validate.Length(min=1, max=len(STAFF_ROLES)))
    temporary_password = password_field(required=False, load_default=None, allow_none=True)
    department_id = fields.UUID(load_default=None, allow_none=True)
    title = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=50))
    specialty = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=120))
    default_consult_minutes = fields.Int(load_default=15, validate=validate.Range(min=1, max=240))


class StaffCreatedSchema(Schema):
    user_id = fields.UUID()
    email = fields.Str()
    roles = fields.List(fields.Str())
