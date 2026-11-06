from marshmallow import Schema, fields, validate


class ScheduleTemplateSchema(Schema):
    id = fields.UUID(dump_only=True)
    doctor_id = fields.UUID(dump_only=True)
    weekday = fields.Int(required=True, validate=validate.Range(min=0, max=6),
                         metadata={"description": "0 = Monday ... 6 = Sunday"})
    start_time = fields.Time(required=True, metadata={"description": "Hospital local time"})
    end_time = fields.Time(required=True, metadata={"description": "Hospital local time"})
    slot_minutes = fields.Int(required=True, validate=validate.Range(min=5, max=240))
    valid_from = fields.Date(required=True)
    valid_until = fields.Date(load_default=None, allow_none=True)


class ScheduleExceptionSchema(Schema):
    id = fields.UUID(dump_only=True)
    doctor_id = fields.UUID(dump_only=True)
    on_date = fields.Date(required=True)
    kind = fields.Str(required=True, validate=validate.OneOf(["unavailable", "extra_session"]))
    start_time = fields.Time(load_default=None, allow_none=True,
                             metadata={"description": "Omit start/end with kind=unavailable to block the whole day"})
    end_time = fields.Time(load_default=None, allow_none=True)
    slot_minutes = fields.Int(load_default=None, allow_none=True, validate=validate.Range(min=5, max=240))
    reason = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=300))


class ExceptionQueryArgs(Schema):
    date_from = fields.Date(load_default=None)


class SlotQueryArgs(Schema):
    date = fields.Date(required=True, metadata={"description": "Hospital local date (YYYY-MM-DD)"})


class SlotSchema(Schema):
    start_at = fields.DateTime()
    end_at = fields.DateTime()
    available = fields.Bool()


class SlotDaySchema(Schema):
    doctor_id = fields.UUID()
    date = fields.Date()
    timezone = fields.Str()
    slots = fields.List(fields.Nested(SlotSchema))
