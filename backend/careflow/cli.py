"""Administrative CLI: `flask --app wsgi careflow <command>`."""

from __future__ import annotations

import re
import secrets

import click
from flask import Flask, current_app
from sqlalchemy import select

from careflow.extensions import db
from careflow.models import Department, DoctorProfile, Hospital, StaffMembership, User
from careflow.models.base import utcnow
from careflow.security.passwords import hash_password
from careflow.services import audit_service
from careflow.services.normalize import normalize_email


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100]


def register_cli(app: Flask) -> None:
    group = click.Group("careflow", help="CareFlow administration commands.")

    @group.command("create-hospital")
    @click.option("--code", required=True, help="Short code, e.g. LUTH (2-10 letters/digits).")
    @click.option("--name", required=True)
    @click.option("--timezone", default="Africa/Lagos", show_default=True)
    @click.option("--address", default=None)
    def create_hospital(code: str, name: str, timezone: str, address: str | None):
        """Create a hospital."""
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone)  # raises if invalid
        hospital = Hospital(code=code.upper(), name=name, slug=_slugify(name), timezone=timezone, address=address)
        db.session.add(hospital)
        db.session.flush()
        audit_service.record("hospital.created", hospital_id=hospital.id, target_type="hospital",
                             target_id=hospital.id, details={"via": "cli"})
        db.session.commit()
        click.echo(f"Created hospital {hospital.code} ({hospital.id})")

    @group.command("create-staff")
    @click.option("--hospital", "hospital_code", required=True, help="Hospital code.")
    @click.option("--email", required=True)
    @click.option("--name", "full_name", required=True)
    @click.option("--role", "roles", multiple=True, required=True,
                  type=click.Choice(["receptionist", "doctor", "hospital_admin"]))
    @click.option("--department", "department_name", default=None, help="Department name (doctors).")
    @click.password_option(help="Initial password (prompted if omitted).")
    def create_staff(hospital_code, email, full_name, roles, department_name, password):
        """Create a staff account. Use this to bootstrap the first hospital_admin."""
        if len(password) < 10:
            raise click.BadParameter("Password must be at least 10 characters.")
        hospital = db.session.scalar(select(Hospital).where(Hospital.code == hospital_code.upper()))
        if hospital is None:
            raise click.ClickException(f"No hospital with code {hospital_code}")
        email = normalize_email(email)
        user = db.session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, full_name=full_name, password_hash=hash_password(password),
                        password_changed_at=utcnow())
            db.session.add(user)
            db.session.flush()
        for role in roles:
            if not db.session.scalar(select(StaffMembership.id).where(
                    StaffMembership.user_id == user.id, StaffMembership.hospital_id == hospital.id,
                    StaffMembership.role == role)):
                db.session.add(StaffMembership(user_id=user.id, hospital_id=hospital.id, role=role))
        if "doctor" in roles:
            dept = db.session.scalar(select(Department).where(
                Department.hospital_id == hospital.id, Department.name == department_name))
            if dept is None:
                raise click.ClickException("Doctors need --department with an existing department name.")
            db.session.add(DoctorProfile(user_id=user.id, hospital_id=hospital.id, department_id=dept.id))
        audit_service.record("staff.granted_roles", hospital_id=hospital.id, target_type="user", target_id=user.id,
                             details={"roles": sorted(roles), "via": "cli"})
        db.session.commit()
        click.echo(f"Staff account {email} has roles {', '.join(sorted(roles))} at {hospital.code}")

    @group.command("seed-dev")
    def seed_dev():
        """Insert synthetic development data. Refuses to run in production."""
        if current_app.config["APP_ENV"] == "production":
            raise click.ClickException("seed-dev must never run in production.")
        from careflow.seed import seed_development_data

        password = seed_development_data()
        click.echo("Seed data created (all accounts use @example.test emails).")
        if password:
            click.echo(f"Generated password for all seed accounts: {password}")

    @group.command("expire-appointments")
    def expire_appointments():
        """Expire unreviewed requests and unanswered proposals whose time has passed. Run every few minutes."""
        from careflow.services.appointment_service import expire_stale

        click.echo(f"Expired {expire_stale()} appointment(s).")

    @group.command("close-past-queues")
    def close_past_queues():
        """Close queues from previous days (unseen patients cancelled, appointments marked no-show). Run nightly."""
        from careflow.services.queue_service import close_past_days

        click.echo(f"Closed {close_past_days()} queue entr(y/ies).")

    @group.command("generate-secret")
    def generate_secret():
        """Print a random value suitable for SECRET_KEY / JWT_SECRET_KEY."""
        click.echo(secrets.token_urlsafe(48))

    app.cli.add_command(group)
