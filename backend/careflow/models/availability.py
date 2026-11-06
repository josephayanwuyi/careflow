"""Doctor availability history. The current status is cached on `doctor_profiles`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from careflow.extensions import db
from careflow.models.base import utcnow


class DoctorStatus:
    OFF_DUTY = "off_duty"
    CHECKED_IN = "checked_in"
    AVAILABLE = "available"
    ON_BREAK = "on_break"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    CHECKED_OUT = "checked_out"

    ALL = (OFF_DUTY, CHECKED_IN, AVAILABLE, ON_BREAK, TEMPORARILY_UNAVAILABLE, CHECKED_OUT)
    ON_DUTY = (CHECKED_IN, AVAILABLE, ON_BREAK, TEMPORARILY_UNAVAILABLE)


class DoctorStatusEvent(db.Model):
    """Append-only. `source` distinguishes what the doctor reported from what staff verified."""

    __tablename__ = "doctor_status_events"
    __table_args__ = (
        CheckConstraint(f"status IN {DoctorStatus.ALL}", name="status_valid"),
        CheckConstraint("source IN ('self_reported', 'staff_verified', 'system')", name="source_valid"),
        Index("ix_doctor_status_events_doctor_time", "doctor_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    expected_return_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())
