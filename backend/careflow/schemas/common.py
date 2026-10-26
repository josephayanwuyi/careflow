from __future__ import annotations

import base64
import uuid
from datetime import datetime

from marshmallow import Schema, fields, validate

from careflow.errors import ValidationFailed


class ErrorBody(Schema):
    code = fields.Str()
    message = fields.Str()
    details = fields.Raw(allow_none=True)
    request_id = fields.Str(allow_none=True)


class ErrorSchema(Schema):
    error = fields.Nested(ErrorBody)


class PaginationArgs(Schema):
    limit = fields.Int(load_default=50, validate=validate.Range(min=1, max=200))
    cursor = fields.Str(load_default=None)


def encode_cursor(ts: datetime, item_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{item_id}".encode()).decode()


def decode_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    try:
        ts, item_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(item_id)
    except (ValueError, UnicodeDecodeError):
        raise ValidationFailed(details={"cursor": ["Invalid cursor."]}) from None


def password_field(**kwargs):
    kwargs.setdefault("required", True)
    return fields.Str(load_only=True, validate=validate.Length(min=10, max=128), **kwargs)


def name_field(**kwargs):
    return fields.Str(validate=validate.Length(min=2, max=200), **kwargs)


def phone_field(**kwargs):
    return fields.Str(validate=validate.Length(min=7, max=20), **kwargs)
