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
    refresh_token = fields.Str(load_default=None, load_only=True, validate=validate.Length(min=20, max=200),
                               metadata={"description": "Omit for browsers: the HttpOnly refresh cookie is used "
                                                        "instead, together with the X-CSRF-Token header."})


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
    refresh_token = fields.Str(allow_none=True, metadata={"description": "null for web clients (cookie instead)"})
    token_type = fields.Str(dump_default="Bearer")
    expires_in = fields.Int()
    user = fields.Nested(MeSchema)


class RegisterHospitalAdminSchema(Schema):
    hospital_name = fields.Str(required=True, validate=validate.Length(min=2, max=200))
    hospital_code = fields.Str(
        required=True,
        validate=validate.Regexp(r"^[A-Z0-9]{2,10}$", error="Code must be 2 to 10 uppercase letters or digits."),
    )
    hospital_slug = fields.Str(load_default=None, allow_none=True)
    timezone = fields.Str(load_default="Africa/Lagos")
    address = fields.Str(load_default=None, allow_none=True)
    admin_name = name_field(required=True)
    admin_email = fields.Email(required=True, validate=validate.Length(max=254))
    admin_password = password_field()
    phone = phone_field(load_default=None, allow_none=True)
    starter_tokens = fields.Int(load_default=2500)
    client_type = fields.Str(load_default=None, validate=validate.OneOf(["web", "desktop"]))

    @validates_schema
    def _password_not_email(self, data, **kwargs):
        if data["admin_password"].strip().lower() == data["admin_email"].strip().lower():
            raise ValidationError({"admin_password": ["Password must not be the same as the email address."]})


class HospitalRegistrationResultSchema(Schema):
    hospital_id = fields.UUID()
    hospital_code = fields.Str()
    hospital_name = fields.Str()
    hospital_slug = fields.Str()
    user_id = fields.UUID()
    admin_email = fields.Str()
    admin_name = fields.Str()
    starter_tokens = fields.Int()
    token = fields.Nested(TokenSchema, allow_none=True)
