from marshmallow import Schema, fields

from careflow.schemas.common import PaginationArgs


class NotificationArgs(PaginationArgs):
    unread_only = fields.Bool(load_default=False)


class NotificationSchema(Schema):
    id = fields.UUID()
    type = fields.Str()
    title = fields.Str()
    body = fields.Str()
    related_type = fields.Str(allow_none=True)
    related_id = fields.UUID(allow_none=True)
    created_at = fields.DateTime()
    read_at = fields.DateTime(allow_none=True)


class NotificationPageSchema(Schema):
    items = fields.List(fields.Nested(NotificationSchema))
    unread_count = fields.Int()
    next_cursor = fields.Str(allow_none=True)


class MarkAllReadSchema(Schema):
    marked = fields.Int()
