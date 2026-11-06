import uuid

from flask import Response
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate

from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import report_service

blp = Blueprint("reports", __name__, url_prefix="/api/v1/hospitals", description="Dashboard and reports (staff)")


class AlertSchema(Schema):
    event_type = fields.Str()
    ticket_number = fields.Int(allow_none=True)
    doctor_name = fields.Str(allow_none=True)
    queue_id = fields.UUID()
    entry_id = fields.UUID(allow_none=True)
    reason = fields.Str(allow_none=True)
    occurred_at = fields.DateTime()


class DashboardSchema(Schema):
    date = fields.Date()
    timezone = fields.Str()
    queue = fields.Dict(keys=fields.Str(), values=fields.Int())
    appointments = fields.Dict(keys=fields.Str(), values=fields.Int())
    doctors = fields.Dict(keys=fields.Str(), values=fields.Int(),
                          metadata={"description": "Number of doctors per patient-facing status"})
    alerts = fields.List(fields.Nested(AlertSchema))


class QueueReportArgs(Schema):
    date = fields.Date(required=True)


class AuditExportArgs(Schema):
    occurred_from = fields.AwareDateTime(load_default=None)
    occurred_to = fields.AwareDateTime(load_default=None)
    action = fields.Str(load_default=None, validate=validate.Length(max=64))
    target_type = fields.Str(load_default=None, validate=validate.Length(max=64))


def _csv(body: str, filename: str) -> Response:
    return Response(body, mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@blp.route("/<uuid:hospital_id>/dashboard", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, DashboardSchema)
@login_required
def get_dashboard(hospital_id: uuid.UUID):
    """Today's counts, pending approvals, doctor availability and recent queue alerts (desk staff)."""
    return report_service.dashboard(current_actor(), hospital_id)


@blp.route("/<uuid:hospital_id>/reports/queue-day.csv", methods=["GET"])
@blp.doc(security=BEARER_SECURITY, responses={"200": {"content": {"text/csv": {}}}})
@blp.arguments(QueueReportArgs, location="query")
@login_required
def queue_day_report(args, hospital_id: uuid.UUID):
    """Queue report for a day as CSV (requires `audit.export`). Patients appear by hospital number only."""
    body = report_service.queue_day_csv(current_actor(), hospital_id, args["date"])
    return _csv(body, f"queue-{args['date'].isoformat()}.csv")


@blp.route("/<uuid:hospital_id>/audit-events/export.csv", methods=["GET"])
@blp.doc(security=BEARER_SECURITY, responses={"200": {"content": {"text/csv": {}}}})
@blp.arguments(AuditExportArgs, location="query")
@login_required
def audit_export(args, hospital_id: uuid.UUID):
    """Audit events as CSV (requires `audit.view` and `audit.export`; last 30 days by default, max 10,000 rows)."""
    return _csv(report_service.audit_csv(current_actor(), hospital_id, **args), "audit-events.csv")
