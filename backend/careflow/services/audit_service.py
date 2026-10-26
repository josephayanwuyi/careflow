"""Audit trail writer and reader.

`record()` adds the event to the current SQLAlchemy session, so it commits (or rolls back)
together with the change it describes.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime

from flask import g, has_request_context, request
from sqlalchemy import and_, or_, select

from careflow.extensions import db
from careflow.models import AuditEvent
from careflow.security.policy import Actor, Perm, require


def _ip_prefix(raw: str | None) -> str | None:
    """Store a truncated network prefix rather than the full client IP (data minimisation)."""
    if not raw:
        return None
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    prefix = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def record(
    action: str,
    *,
    actor: Actor | None = None,
    actor_user_id: uuid.UUID | None = None,
    hospital_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    reason: str | None = None,
    details: dict | None = None,
) -> AuditEvent:
    roles = None
    if actor is not None:
        actor_user_id = actor.user_id
        if hospital_id is not None:
            roles = ",".join(sorted(actor.roles_in(hospital_id))) or None
        if roles is None and actor.patient_id is not None:
            roles = "patient"
    event = AuditEvent(
        action=action,
        actor_user_id=actor_user_id,
        actor_roles=roles,
        hospital_id=hospital_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        reason=reason,
        details=details or {},
        request_id=g.get("request_id") if has_request_context() else None,
        ip_prefix=_ip_prefix(request.remote_addr) if has_request_context() else None,
    )
    db.session.add(event)
    return event


def list_events(
    actor: Actor,
    hospital_id: uuid.UUID,
    *,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> list[AuditEvent]:
    """Newest first, keyset-paginated on (occurred_at, id)."""
    require(actor, Perm.AUDIT_VIEW, hospital_id)
    stmt = select(AuditEvent).where(AuditEvent.hospital_id == hospital_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if actor_user_id:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if occurred_from:
        stmt = stmt.where(AuditEvent.occurred_at >= occurred_from)
    if occurred_to:
        stmt = stmt.where(AuditEvent.occurred_at < occurred_to)
    if cursor:
        ts, last_id = cursor
        stmt = stmt.where(or_(AuditEvent.occurred_at < ts, and_(AuditEvent.occurred_at == ts, AuditEvent.id < last_id)))
    stmt = stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit)
    return list(db.session.scalars(stmt))
