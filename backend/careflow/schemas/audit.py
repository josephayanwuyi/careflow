from marshmallow import Schema, fields, validate

from careflow.schemas.common import PaginationArgs


class AuditQueryArgs(PaginationArgs):
    action = fields.Str(load_default=None, validate=validate.Length(max=64))
    target_type = fields.Str(load_default=None, validate=validate.Length(max=64))
    target_id = fields.Str(load_default=None, validate=validate.Length(max=64))
    actor_user_id = fields.UUID(load_default=None)
    occurred_from = fields.AwareDateTime(load_default=None)
    occurred_to = fields.AwareDateTime(load_default=None)


class AuditEventSchema(Schema):
    id = fields.UUID()
    occurred_at = fields.DateTime()
    actor_user_id = fields.UUID(allow_none=True)
    actor_roles = fields.Str(allow_none=True)
    action = fields.Str()
    target_type = fields.Str(allow_none=True)
    target_id = fields.Str(allow_none=True)
    reason = fields.Str(allow_none=True)
    request_id = fields.Str(allow_none=True)
    details = fields.Dict()


class AuditPageSchema(Schema):
    items = fields.List(fields.Nested(AuditEventSchema))
    next_cursor = fields.Str(allow_none=True)
