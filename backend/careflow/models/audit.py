from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from careflow.extensions import db
from careflow.models.base import utcnow


class AuditEvent(db.Model):
    """Append-only audit trail. UPDATE and DELETE are rejected by a database trigger."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_hospital_time", "hospital_id", "occurred_at"),
        Index("ix_audit_events_target", "target_type", "target_id"),
        Index("ix_audit_events_actor_time", "actor_user_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    actor_roles: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_prefix: Mapped[str | None] = mapped_column(String(64))
    # Non-sensitive context only (IDs, statuses). Never names, tokens or passwords.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
