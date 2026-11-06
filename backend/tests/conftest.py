"""Test fixtures. Tests run against a real PostgreSQL database (TEST_DATABASE_URL).

The schema is built by running the real Alembic migrations (downgrade to base, then
upgrade), so migrations are exercised on every test run. Tables are truncated after
each test. All data is synthetic.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
os.environ["APP_ENV"] = "testing"
os.environ["RATELIMIT_ENABLED"] = "true"

from argon2 import PasswordHasher  # noqa: E402

import careflow.security.passwords as _passwords  # noqa: E402

# Production-strength Argon2 makes the suite slow; tests only need a valid Argon2id hash.
_passwords._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)

from careflow import create_app  # noqa: E402
from careflow.extensions import db, limiter  # noqa: E402
from careflow.models import (  # noqa: E402
    Department,
    DoctorProfile,
    DoctorScheduleTemplate,
    Hospital,
    StaffMembership,
    User,
)
from careflow.security.passwords import hash_password  # noqa: E402

PASSWORD = "correct-horse-battery"
_PW_HASH = hash_password(PASSWORD)


@pytest.fixture(scope="session")
def app():
    app = create_app("testing")
    with app.app_context():
        import flask_migrate

        flask_migrate.downgrade(revision="base")
        flask_migrate.upgrade()
    return app


@pytest.fixture(autouse=True)
def _clean_db(app):
    # Rate limiting is off by default in tests; test_platform turns it on explicitly.
    limiter.enabled = False
    yield
    with app.app_context():
        db.session.remove()
        tables = ", ".join(t.name for t in db.metadata.sorted_tables)
        db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.session.commit()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


# --- factories -----------------------------------------------------------------------


class Factory:
    def __init__(self, app):
        self.app = app

    def hospital(self, code: str = "HOSA", name: str | None = None) -> uuid.UUID:
        with self.app.app_context():
            h = Hospital(code=code, name=name or f"Hospital {code}", slug=code.lower())
            db.session.add(h)
            db.session.commit()
            return h.id

    def department(self, hospital_id: uuid.UUID, name: str = "General Outpatient") -> uuid.UUID:
        with self.app.app_context():
            d = Department(hospital_id=hospital_id, name=name)
            db.session.add(d)
            db.session.commit()
            return d.id

    def staff(self, hospital_id: uuid.UUID, role: str, email: str | None = None,
              department_id: uuid.UUID | None = None) -> dict:
        email = email or f"{role}-{uuid.uuid4().hex[:8]}@example.test"
        with self.app.app_context():
            u = User(email=email, full_name=f"Test {role}", password_hash=_PW_HASH)
            db.session.add(u)
            db.session.flush()
            db.session.add(StaffMembership(user_id=u.id, hospital_id=hospital_id, role=role))
            doctor_id = None
            if role == "doctor":
                if department_id is None:
                    department_id = Department(hospital_id=hospital_id, name=f"Dept {uuid.uuid4().hex[:6]}")
                    db.session.add(department_id)
                    db.session.flush()
                    department_id = department_id.id
                doc = DoctorProfile(user_id=u.id, hospital_id=hospital_id, department_id=department_id)
                db.session.add(doc)
                db.session.flush()
                doctor_id = doc.id
            db.session.commit()
            return {"user_id": u.id, "email": email, "doctor_id": doctor_id}


    def schedule(self, hospital_id: uuid.UUID, doctor_id: uuid.UUID, *, start: time = time(9, 0),
                 end: time = time(12, 0), slot_minutes: int = 15, weekdays=range(7)) -> None:
        """Give a doctor a session every day (by default) from a week ago, open-ended."""
        with self.app.app_context():
            for wd in weekdays:
                db.session.add(DoctorScheduleTemplate(
                    hospital_id=hospital_id, doctor_id=doctor_id, weekday=wd, start_time=start, end_time=end,
                    slot_minutes=slot_minutes, valid_from=date.today() - timedelta(days=7)))
            db.session.commit()

    def approved_appointment(self, hospital_id: uuid.UUID, doctor_id: uuid.UUID, patient_id, *,
                             start_at: datetime, minutes: int = 1) -> str:
        """Insert an approved appointment directly (bypassing slot rules) for queue tests."""
        from careflow.models import Appointment
        from careflow.services.patient_service import ensure_hospital_record

        with self.app.app_context():
            doctor = db.session.get(DoctorProfile, doctor_id)
            ensure_hospital_record(hospital_id, uuid.UUID(str(patient_id)), None)
            appt = Appointment(hospital_id=hospital_id, department_id=doctor.department_id, doctor_id=doctor_id,
                               patient_id=uuid.UUID(str(patient_id)), start_at=start_at,
                               end_at=start_at + timedelta(minutes=minutes), status="approved", source="staff_booked")
            db.session.add(appt)
            db.session.commit()
            return str(appt.id)

    def set_settings(self, hospital_id: uuid.UUID, **settings) -> None:
        with self.app.app_context():
            h = db.session.get(Hospital, hospital_id)
            h.settings = {**(h.settings or {}), **settings}
            db.session.commit()

    def clinic(self, code: str = "HOSA") -> dict:
        """A hospital with one department, a scheduled doctor, a receptionist and an admin."""
        hid = self.hospital(code)
        dept = self.department(hid)
        doctor = self.staff(hid, "doctor", department_id=dept)
        self.schedule(hid, doctor["doctor_id"])
        return {"hospital_id": hid, "department_id": dept, "doctor": doctor,
                "receptionist": self.staff(hid, "receptionist"), "admin": self.staff(hid, "hospital_admin")}


LAGOS = ZoneInfo("Africa/Lagos")


def slot_time(days_ahead: int = 7, hour: int = 9, minute: int = 0) -> str:
    """ISO start time of a slot in Africa/Lagos, `days_ahead` days from today."""
    d = datetime.now(LAGOS).date() + timedelta(days=days_ahead)
    return datetime.combine(d, time(hour, minute), tzinfo=LAGOS).isoformat()


@pytest.fixture
def factory(app):
    return Factory(app)


def register_patient(client, email: str | None = None, **extra) -> dict:
    email = email or f"patient-{uuid.uuid4().hex[:8]}@example.test"
    body = {"email": email, "password": PASSWORD, "full_name": "Synthetic Patient", **extra}
    resp = client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.get_json()
    return {"email": email, **resp.get_json()}


def login(client, email: str, password: str = PASSWORD) -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def auth_headers(client, email: str, password: str = PASSWORD) -> dict:
    return {"Authorization": f"Bearer {login(client, email, password)['access_token']}"}
