"""Synthetic development data. Every record is clearly marked as seed data.

- The hospital has `is_seed_data = true` and code `DEMO`.
- All accounts use the reserved `example.test` email domain.
- Patient names are obviously fictional.
"""

from __future__ import annotations

import os
import secrets
from datetime import date

from sqlalchemy import select

from careflow.extensions import db
from careflow.models import Department, DoctorProfile, Hospital, PatientProfile, StaffMembership, User
from careflow.models.base import utcnow
from careflow.security.passwords import hash_password
from careflow.services.patient_service import ensure_hospital_record

DEPARTMENTS = ["General Outpatient", "Paediatrics", "Obstetrics & Gynaecology"]

STAFF = [
    # email, name, roles, department, title
    ("admin@example.test", "Demo Admin", ["hospital_admin"], None, None),
    ("reception1@example.test", "Demo Receptionist One", ["receptionist"], None, None),
    ("reception2@example.test", "Demo Receptionist Two", ["receptionist"], None, None),
    ("dr.adeyemi@example.test", "Test Adeyemi", ["doctor"], "General Outpatient", "Dr."),
    ("dr.okafor@example.test", "Test Okafor", ["doctor"], "General Outpatient", "Dr."),
    ("dr.bello@example.test", "Test Bello", ["doctor"], "Paediatrics", "Dr."),
]

PATIENTS = [
    ("patient1@example.test", "Synthetic Patient One", "+2348000000001", date(1990, 1, 15)),
    ("patient2@example.test", "Synthetic Patient Two", "+2348000000002", date(1985, 6, 2)),
    ("patient3@example.test", "Synthetic Patient Three", "+2348000000003", date(2001, 11, 30)),
]


def seed_development_data() -> str | None:
    """Idempotent: re-running does not duplicate records. Returns the password if one was generated."""
    generated = None
    password = os.environ.get("SEED_PASSWORD")
    if not password:
        password = generated = secrets.token_urlsafe(12)
    pw_hash = hash_password(password)

    hospital = db.session.scalar(select(Hospital).where(Hospital.code == "DEMO"))
    if hospital is None:
        hospital = Hospital(code="DEMO", name="CareFlow Demo General Hospital (SEED DATA)", slug="careflow-demo",
                            timezone="Africa/Lagos", is_seed_data=True, address="Synthetic address, not real")
        db.session.add(hospital)
        db.session.flush()

    depts: dict[str, Department] = {}
    for name in DEPARTMENTS:
        dept = db.session.scalar(select(Department).where(Department.hospital_id == hospital.id, Department.name == name))
        if dept is None:
            dept = Department(hospital_id=hospital.id, name=name)
            db.session.add(dept)
            db.session.flush()
        depts[name] = dept

    def get_or_create_user(email: str, name: str, phone: str | None = None) -> User:
        user = db.session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, full_name=name, phone=phone, password_hash=pw_hash, password_changed_at=utcnow())
            db.session.add(user)
            db.session.flush()
        else:
            user.password_hash = pw_hash
        return user

    for email, name, roles, dept_name, title in STAFF:
        user = get_or_create_user(email, name)
        for role in roles:
            if not db.session.scalar(select(StaffMembership.id).where(
                    StaffMembership.user_id == user.id, StaffMembership.hospital_id == hospital.id,
                    StaffMembership.role == role)):
                db.session.add(StaffMembership(user_id=user.id, hospital_id=hospital.id, role=role))
        if dept_name and not db.session.scalar(select(DoctorProfile.id).where(
                DoctorProfile.user_id == user.id, DoctorProfile.hospital_id == hospital.id)):
            db.session.add(DoctorProfile(user_id=user.id, hospital_id=hospital.id, department_id=depts[dept_name].id,
                                         title=title, specialty=dept_name))

    for email, name, phone, dob in PATIENTS:
        user = get_or_create_user(email, name, phone)
        profile = db.session.scalar(select(PatientProfile).where(PatientProfile.user_id == user.id))
        if profile is None:
            profile = PatientProfile(user_id=user.id, full_name=name, phone=phone, date_of_birth=dob)
            db.session.add(profile)
            db.session.flush()
        ensure_hospital_record(hospital.id, profile.id, None)

    db.session.commit()
    return generated
