from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from careflow.schemas.common import name_field, password_field, phone_field


class RegisterPatientSchema(Schema):
    email = fields.Email(required=True, validate=validate.Length(max=254))
    password = password_field()
    full_name = name_field(required=True)
    phone = phone_field(load_default=None, allow_none=True)
    date_of_birth = fields.Date(load_default=None, allow_none=True)

    @validates_schema
    def _password_not_email(self, data, **kwargs):
        if data["password"].strip().lower() == data["email"].strip().lower():
            raise ValidationError({"password": ["Password must not be the same as the email address."]})


class LoginSchema(Schema):
    email = fields.Email(required=True)
    password = fields.Str(required=True, load_only=True, validate=validate.Length(min=1, max=128))
    client_type = fields.Str(load_default=None, validate=validate.OneOf(["web", "desktop"]))


class RefreshSchema(Schema):
    refresh_token = fields.Str(required=True, load_only=True, validate=validate.Length(min=20, max=200))


class ChangePasswordSchema(Schema):
    current_password = fields.Str(required=True, load_only=True, validate=validate.Length(min=1, max=128))
    new_password = password_field()


class MembershipSchema(Schema):
    hospital_id = fields.UUID()
    hospital_name = fields.Str()
    roles = fields.List(fields.Str())
    permissions = fields.List(fields.Str())
    doctor_id = fields.UUID(allow_none=True)


class MeSchema(Schema):
    id = fields.UUID()
    email = fields.Str()
    full_name = fields.Str()
    phone = fields.Str(allow_none=True)
    patient_id = fields.UUID(allow_none=True)
    memberships = fields.List(fields.Nested(MembershipSchema))


class TokenSchema(Schema):
    access_token = fields.Str()
    refresh_token = fields.Str()
    token_type = fields.Str(dump_default="Bearer")
    expires_in = fields.Int()
    user = fields.Nested(MeSchema)
