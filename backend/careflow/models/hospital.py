from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careflow.extensions import db
from careflow.models.base import Timestamps, UUIDPrimaryKey


class Hospital(UUIDPrimaryKey, Timestamps, db.Model):
    __tablename__ = "hospitals"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z0-9]{2,10}$'", name="code_format"),
        CheckConstraint("next_patient_number > 0", name="next_patient_number_positive"),
    )

    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Africa/Lagos")
    address: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # True for synthetic development data created by the seed script.
    is_seed_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Operational policies (check-in window, grace minutes, missed-turn rules, ...).
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # Counter for hospital-issued patient numbers; incremented under row lock.
    next_patient_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    departments: Mapped[list[Department]] = relationship(back_populates="hospital", order_by="Department.name")


class Department(UUIDPrimaryKey, Timestamps, db.Model):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("hospital_id", "name"),
        # Target for composite foreign keys that guarantee same-hospital references.
        UniqueConstraint("id", "hospital_id"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    hospital: Mapped[Hospital] = relationship(back_populates="departments")
