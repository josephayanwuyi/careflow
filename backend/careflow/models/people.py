"""Doctor and patient profiles."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careflow.extensions import db
from careflow.models.base import Timestamps, UUIDPrimaryKey
from careflow.models.hospital import Department, Hospital
from careflow.models.user import User


class DoctorProfile(UUIDPrimaryKey, Timestamps, db.Model):
    __tablename__ = "doctor_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "hospital_id"),
        UniqueConstraint("id", "hospital_id"),
        # Department must belong to the same hospital as the doctor.
        ForeignKeyConstraint(
            ["department_id", "hospital_id"],
            ["departments.id", "departments.hospital_id"],
            name="fk_doctor_profiles_department_same_hospital",
        ),
        CheckConstraint("default_consult_minutes BETWEEN 1 AND 240", name="consult_minutes_range"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False, index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(50))
    specialty: Mapped[str | None] = mapped_column(String(120))
    default_consult_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    is_accepting_appointments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship()
    hospital: Mapped[Hospital] = relationship()
    department: Mapped[Department] = relationship(foreign_keys=[department_id], primaryjoin="DoctorProfile.department_id == Department.id", viewonly=True)


class PatientProfile(UUIDPrimaryKey, Timestamps, db.Model):
    """Minimal patient identity. Not a medical record.

    `user_id` is null for walk-in patients registered by staff who have no online account.
    """

    __tablename__ = "patient_profiles"
    __table_args__ = (
        CheckConstraint("sex IS NULL OR sex IN ('female', 'male', 'other', 'undisclosed')", name="sex_valid"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(16))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    user: Mapped[User | None] = relationship(foreign_keys=[user_id])


class HospitalPatientRecord(UUIDPrimaryKey, Timestamps, db.Model):
    """Links a patient to a hospital and holds the hospital-issued patient number.

    A hospital's staff can only see patients that have a record at that hospital.
    """

    __tablename__ = "hospital_patient_records"
    __table_args__ = (
        UniqueConstraint("hospital_id", "patient_id"),
        UniqueConstraint("hospital_id", "hospital_patient_number"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient_profiles.id", ondelete="RESTRICT"), nullable=False, index=True)
    hospital_patient_number: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    patient: Mapped[PatientProfile] = relationship()
