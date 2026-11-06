"""Doctor working sessions, used to generate bookable appointment slots."""

from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column

from careflow.extensions import db
from careflow.models.base import Timestamps, UUIDPrimaryKey


class DoctorScheduleTemplate(UUIDPrimaryKey, Timestamps, db.Model):
    """A recurring weekly session, e.g. Mondays 09:00-13:00 in 15-minute slots (hospital local time)."""

    __tablename__ = "doctor_schedule_templates"
    __table_args__ = (
        ForeignKeyConstraint(["doctor_id", "hospital_id"], ["doctor_profiles.id", "doctor_profiles.hospital_id"],
                             name="fk_schedule_templates_doctor_same_hospital"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("start_time < end_time", name="start_before_end"),
        CheckConstraint("slot_minutes BETWEEN 5 AND 240", name="slot_minutes_range"),
        CheckConstraint("valid_until IS NULL OR valid_until >= valid_from", name="valid_range"),
        Index("ix_schedule_templates_doctor_weekday", "doctor_id", "weekday"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0 = Monday
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    slot_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DoctorScheduleException(UUIDPrimaryKey, Timestamps, db.Model):
    """A one-off change on a date: the doctor is unavailable (all day or a period), or an extra session."""

    __tablename__ = "doctor_schedule_exceptions"
    __table_args__ = (
        ForeignKeyConstraint(["doctor_id", "hospital_id"], ["doctor_profiles.id", "doctor_profiles.hospital_id"],
                             name="fk_schedule_exceptions_doctor_same_hospital"),
        CheckConstraint("kind IN ('unavailable', 'extra_session')", name="kind_valid"),
        CheckConstraint("(start_time IS NULL) = (end_time IS NULL)", name="times_together"),
        CheckConstraint("start_time IS NULL OR start_time < end_time", name="start_before_end"),
        CheckConstraint(
            "kind <> 'extra_session' OR (start_time IS NOT NULL AND slot_minutes BETWEEN 5 AND 240)",
            name="extra_session_complete",
        ),
        Index("ix_schedule_exceptions_doctor_date", "doctor_id", "on_date"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    on_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time)  # null with kind=unavailable means the whole day
    end_time: Mapped[time | None] = mapped_column(Time)
    slot_minutes: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(300))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
