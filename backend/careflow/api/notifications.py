import uuid

from flask_smorest import Blueprint

from careflow.schemas.common import decode_cursor, encode_cursor
from careflow.schemas.notification import MarkAllReadSchema, NotificationArgs, NotificationPageSchema, NotificationSchema
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import notification_service

blp = Blueprint("notifications", __name__, url_prefix="/api/v1/notifications", description="In-app notifications")


@blp.route("", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(NotificationArgs, location="query")
@blp.response(200, NotificationPageSchema)
@login_required
def list_notifications(args):
    actor = current_actor()
    items = notification_service.list_for_user(actor, unread_only=args["unread_only"], limit=args["limit"],
                                               cursor=decode_cursor(args["cursor"]))
    next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if len(items) == args["limit"] else None
    return {"items": items, "unread_count": notification_service.unread_count(actor), "next_cursor": next_cursor}


@blp.route("/<uuid:notification_id>/read", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, NotificationSchema)
@login_required
def mark_read(notification_id: uuid.UUID):
    return notification_service.mark_read(current_actor(), notification_id)


@blp.route("/read-all", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, MarkAllReadSchema)
@login_required
def mark_all_read():
    return {"marked": notification_service.mark_all_read(current_actor())}
