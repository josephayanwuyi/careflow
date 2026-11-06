"""Live queues: one queue per doctor per hospital-local service date."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careflow.extensions import db
from careflow.models.base import Timestamps, UUIDPrimaryKey, utcnow
from careflow.models.people import DoctorProfile, PatientProfile


class QueueStatus:
    WAITING = "waiting"
    CALLED = "called"
    IN_CONSULTATION = "in_consultation"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"
    TRANSFERRED = "transferred"

    ALL = (WAITING, CALLED, IN_CONSULTATION, COMPLETED, MISSED, CANCELLED, TRANSFERRED)
    # Entries that still occupy the patient's place for the day.
    ACTIVE = (WAITING, CALLED, IN_CONSULTATION, MISSED)
    # At most one entry per queue may be with the doctor (called or being seen).
    WITH_DOCTOR = (CALLED, IN_CONSULTATION)


_ACTIVE_SQL = "status IN ('waiting', 'called', 'in_consultation', 'missed')"


class Queue(UUIDPrimaryKey, Timestamps, db.Model):
    __tablename__ = "queues"
    __table_args__ = (
        UniqueConstraint("doctor_id", "service_date"),
        UniqueConstraint("id", "hospital_id"),
        ForeignKeyConstraint(["doctor_id", "hospital_id"], ["doctor_profiles.id", "doctor_profiles.hospital_id"],
                             name="fk_queues_doctor_same_hospital"),
        CheckConstraint("next_ticket_number > 0", name="next_ticket_positive"),
        Index("ix_queues_hospital_date", "hospital_id", "service_date"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)  # hospital-local date
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paused_reason: Mapped[str | None] = mapped_column(String(300))
    next_ticket_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    doctor: Mapped[DoctorProfile] = relationship(
        primaryjoin="Queue.doctor_id == DoctorProfile.id", foreign_keys=[doctor_id], viewonly=True)


class QueueEntry(UUIDPrimaryKey, Timestamps, db.Model):
    """A patient's place in a queue. `id` is the stable identifier; `ticket_number` is display-only."""

    __tablename__ = "queue_entries"
    __table_args__ = (
        ForeignKeyConstraint(["queue_id", "hospital_id"], ["queues.id", "queues.hospital_id"],
                             name="fk_queue_entries_queue_same_hospital"),
        CheckConstraint(f"status IN {QueueStatus.ALL}", name="status_valid"),
        CheckConstraint("kind IN ('appointment', 'walk_in')", name="kind_valid"),
        CheckConstraint("(kind = 'appointment') = (appointment_id IS NOT NULL)", name="appointment_matches_kind"),
        CheckConstraint("priority_level BETWEEN 0 AND 3", name="priority_range"),
        CheckConstraint("priority_level = 0 OR priority_reason IS NOT NULL", name="priority_has_reason"),
        UniqueConstraint("queue_id", "ticket_number"),
        # One live entry per appointment.
        Index("uq_queue_entries_active_appointment", "appointment_id", unique=True,
              postgresql_where=text(f"appointment_id IS NOT NULL AND {_ACTIVE_SQL}")),
        # A patient can only hold one live place per day.
        Index("uq_queue_entries_active_patient_day", "patient_id", "service_date", unique=True,
              postgresql_where=text(_ACTIVE_SQL)),
        # Only one patient per queue can be called or in consultation: prevents duplicate calls.
        Index("uq_queue_entries_one_with_doctor", "queue_id", unique=True,
              postgresql_where=text("status IN ('called', 'in_consultation')")),
        Index("ix_queue_entries_queue_order", "queue_id", "status", "priority_level", "sort_key"),
    )

    queue_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ticket_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    priority_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    priority_reason: Mapped[str | None] = mapped_column(String(300))
    priority_set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # Ordering key within a priority level (see services/queue_rules.py).
    sort_key: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_in_method: Mapped[str] = mapped_column(String(16), nullable=False)  # self | staff
    arrival_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrival_confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    entered_queue_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    missed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(300))
    transferred_to_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("queue_entries.id", ondelete="RESTRICT"))
    # Estimate shown when the patient joined, kept to measure prediction error later.
    initial_estimate_min: Mapped[int | None] = mapped_column(Integer)
    initial_estimate_max: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    queue: Mapped[Queue] = relationship(primaryjoin="QueueEntry.queue_id == Queue.id", foreign_keys=[queue_id],
                                        viewonly=True)
    patient: Mapped[PatientProfile] = relationship(viewonly=True)


class QueueEvent(db.Model):
    """Append-only, timestamped record of every queue transition. Never contains names."""

    __tablename__ = "queue_events"
    __table_args__ = (
        Index("ix_queue_events_queue_time", "queue_id", "occurred_at"),
        Index("ix_queue_events_entry_time", "entry_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    queue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("queues.id", ondelete="RESTRICT"), nullable=False)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("queue_entries.id", ondelete="RESTRICT"))
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())


class ConsultationSession(UUIDPrimaryKey, Timestamps, db.Model):
    """Start/end of a consultation; the source of historical durations for wait estimates."""

    __tablename__ = "consultation_sessions"
    __table_args__ = (
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="end_after_start"),
        Index("ix_consultations_doctor_ended", "doctor_id", "ended_at"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("queue_entries.id", ondelete="RESTRICT"), unique=True,
                                                nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), nullable=False)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
