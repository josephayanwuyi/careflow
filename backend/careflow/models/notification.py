from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from careflow.extensions import db
from careflow.models.base import Timestamps, UUIDPrimaryKey


class Notification(UUIDPrimaryKey, Timestamps, db.Model):
    """In-app notification. Bodies must not contain clinical information."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_unread", "user_id", postgresql_where=text("read_at IS NULL")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"))
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    related_type: Mapped[str | None] = mapped_column(String(32))
    related_id: Mapped[uuid.UUID | None] = mapped_column()
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
