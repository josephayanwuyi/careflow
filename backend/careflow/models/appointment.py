"""Appointments and their status history."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careflow.extensions import db
from careflow.models.base import Timestamps, UUIDPrimaryKey, utcnow
from careflow.models.hospital import Department, Hospital
from careflow.models.people import DoctorProfile, PatientProfile


class AppointmentStatus:
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESCHEDULE_PROPOSED = "reschedule_proposed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    NO_SHOW = "no_show"

    ALL = (REQUESTED, APPROVED, REJECTED, RESCHEDULE_PROPOSED, CANCELLED, EXPIRED, CHECKED_IN, COMPLETED, NO_SHOW)
    TERMINAL = frozenset({REJECTED, CANCELLED, EXPIRED, COMPLETED, NO_SHOW})


# The time range an appointment currently reserves on the doctor's (and patient's) calendar.
# Pending requests reserve nothing; a staff reschedule proposal reserves the proposed time.
HELD_RANGE_SQL = (
    "CASE "
    "WHEN status IN ('approved', 'checked_in') THEN tstzrange(start_at, end_at, '[)') "
    "WHEN status = 'reschedule_proposed' THEN tstzrange(proposed_start_at, proposed_end_at, '[)') "
    "END"
)


class Appointment(UUIDPrimaryKey, Timestamps, db.Model):
    __tablename__ = "appointments"
    __table_args__ = (
        ForeignKeyConstraint(["doctor_id", "hospital_id"], ["doctor_profiles.id", "doctor_profiles.hospital_id"],
                             name="fk_appointments_doctor_same_hospital"),
        ForeignKeyConstraint(["department_id", "hospital_id"], ["departments.id", "departments.hospital_id"],
                             name="fk_appointments_department_same_hospital"),
        CheckConstraint(f"status IN {AppointmentStatus.ALL}", name="status_valid"),
        CheckConstraint("start_at < end_at", name="start_before_end"),
        CheckConstraint("source IN ('patient_request', 'staff_booked')", name="source_valid"),
        CheckConstraint(
            "status <> 'reschedule_proposed' OR (proposed_start_at IS NOT NULL AND proposed_end_at > proposed_start_at)",
            name="proposal_complete",
        ),
        # No double-booking: a doctor cannot hold two overlapping reservations...
        ExcludeConstraint(("doctor_id", "="), ("held_range", "&&"), using="gist",
                          where=text("held_range IS NOT NULL"), name="ex_appointments_doctor_no_overlap"),
        # ...and neither can a patient.
        ExcludeConstraint(("patient_id", "="), ("held_range", "&&"), using="gist",
                          where=text("held_range IS NOT NULL"), name="ex_appointments_patient_no_overlap"),
        # The same patient cannot have two open requests for the same doctor and time.
        Index("uq_appointments_open_request", "patient_id", "doctor_id", "start_at", unique=True,
              postgresql_where=text("status = 'requested'")),
        # Retried submissions with the same key return the original appointment.
        Index("uq_appointments_idempotency", "patient_id", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
        Index("ix_appointments_hospital_status_start", "hospital_id", "status", "start_at"),
        Index("ix_appointments_doctor_start", "doctor_id", "start_at"),
        Index("ix_appointments_patient_start", "patient_id", "start_at"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_for_visit: Mapped[str | None] = mapped_column(String(300))

    proposed_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposed_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    held_range = mapped_column(TSTZRANGE, Computed(HELD_RANGE_SQL, persisted=True))

    status_reason: Mapped[str | None] = mapped_column(String(500))  # last rejection/cancellation/proposal reason
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    hospital: Mapped[Hospital] = relationship(viewonly=True)
    patient: Mapped[PatientProfile] = relationship(viewonly=True)
    doctor: Mapped[DoctorProfile] = relationship(
        primaryjoin="Appointment.doctor_id == DoctorProfile.id", foreign_keys=[doctor_id], viewonly=True)
    department: Mapped[Department] = relationship(
        primaryjoin="Appointment.department_id == Department.id", foreign_keys=[department_id], viewonly=True)


class AppointmentStatusHistory(db.Model):
    """Append-only record of every appointment state change."""

    __tablename__ = "appointment_status_history"
    __table_args__ = (Index("ix_appointment_history_appointment_time", "appointment_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    actor_role: Mapped[str | None] = mapped_column(String(32))  # patient | receptionist | hospital_admin | doctor | system
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())
