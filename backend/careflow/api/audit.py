import uuid

from flask_smorest import Blueprint

from careflow.schemas.audit import AuditPageSchema, AuditQueryArgs
from careflow.schemas.common import decode_cursor, encode_cursor
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import audit_service

blp = Blueprint("audit", __name__, url_prefix="/api/v1/hospitals", description="Audit history")


@blp.route("/<uuid:hospital_id>/audit-events", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(AuditQueryArgs, location="query")
@blp.response(200, AuditPageSchema)
@login_required
def list_audit_events(args, hospital_id: uuid.UUID):
    """Audit events for a hospital, newest first. Requires `audit.view`."""
    limit = args.pop("limit")
    cursor = decode_cursor(args.pop("cursor"))
    items = audit_service.list_events(current_actor(), hospital_id, limit=limit, cursor=cursor, **args)
    next_cursor = encode_cursor(items[-1].occurred_at, items[-1].id) if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}
