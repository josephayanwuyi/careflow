"""Hospitals, departments, doctors and staff accounts."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from careflow.errors import Conflict, NotFound, ValidationFailed
from careflow.extensions import db
from careflow.models import Department, DoctorProfile, Hospital, StaffMembership, User
from careflow.models.base import utcnow
from careflow.security.passwords import hash_password
from careflow.security.policy import Actor, Perm, require
from careflow.services import audit_service
from careflow.services.normalize import normalize_email, normalize_phone


def list_hospitals() -> list[Hospital]:
    return list(db.session.scalars(select(Hospital).where(Hospital.is_active.is_(True)).order_by(Hospital.name)))


def get_hospital(hospital_id: uuid.UUID) -> Hospital:
    hospital = db.session.get(Hospital, hospital_id)
    if hospital is None or not hospital.is_active:
        raise NotFound("Hospital not found.")
    return hospital


def list_departments(hospital_id: uuid.UUID) -> list[Department]:
    get_hospital(hospital_id)
    return list(db.session.scalars(
        select(Department)
        .where(Department.hospital_id == hospital_id, Department.is_active.is_(True))
        .order_by(Department.name)
    ))


def get_department(hospital_id: uuid.UUID, department_id: uuid.UUID) -> Department:
    dept = db.session.get(Department, department_id)
    if dept is None or dept.hospital_id != hospital_id:
        raise NotFound("Department not found.")
    return dept


def create_department(actor: Actor, hospital_id: uuid.UUID, *, name: str, description: str | None) -> Department:
    get_hospital(hospital_id)
    require(actor, Perm.DEPARTMENTS_MANAGE, hospital_id)
    dept = Department(hospital_id=hospital_id, name=name.strip(), description=description)
    db.session.add(dept)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise Conflict("A department with this name already exists.", code="department_exists") from None
    audit_service.record("department.created", actor=actor, hospital_id=hospital_id,
                         target_type="department", target_id=dept.id)
    db.session.commit()
    return dept


def list_doctors(hospital_id: uuid.UUID, department_id: uuid.UUID | None = None) -> list[DoctorProfile]:
    get_hospital(hospital_id)
    stmt = (
        select(DoctorProfile)
        .join(User, User.id == DoctorProfile.user_id)
        .where(DoctorProfile.hospital_id == hospital_id, DoctorProfile.is_active.is_(True), User.is_active.is_(True))
        .order_by(User.full_name)
    )
    if department_id:
        stmt = stmt.where(DoctorProfile.department_id == department_id)
    return list(db.session.scalars(stmt))


def get_doctor(doctor_id: uuid.UUID) -> DoctorProfile:
    doctor = db.session.get(DoctorProfile, doctor_id)
    if doctor is None or not doctor.is_active or not doctor.user.is_active or not doctor.hospital.is_active:
        raise NotFound("Doctor not found.")
    return doctor


def create_staff(
    actor: Actor,
    hospital_id: uuid.UUID,
    *,
    email: str,
    full_name: str,
    phone: str | None,
    roles: list[str],
    temporary_password: str | None,
    department_id: uuid.UUID | None = None,
    title: str | None = None,
    specialty: str | None = None,
    default_consult_minutes: int = 15,
) -> User:
    """Create a staff account (or add roles to an existing account) for a hospital."""
    get_hospital(hospital_id)
    require(actor, Perm.STAFF_MANAGE, hospital_id)
    if "doctor" in roles and department_id is None:
        raise ValidationFailed(details={"department_id": ["Required for doctors."]})
    if department_id is not None:
        get_department(hospital_id, department_id)

    email = normalize_email(email)
    user = db.session.scalar(select(User).where(User.email == email))
    if user is None:
        if not temporary_password:
            raise ValidationFailed(details={"temporary_password": ["Required for a new account."]})
        user = User(email=email, full_name=full_name.strip(), phone=normalize_phone(phone) if phone else None,
                    password_hash=hash_password(temporary_password), password_changed_at=utcnow())
        db.session.add(user)
        db.session.flush()

    existing = set(db.session.scalars(
        select(StaffMembership.role).where(StaffMembership.user_id == user.id, StaffMembership.hospital_id == hospital_id)
    ))
    for role in roles:
        if role not in existing:
            db.session.add(StaffMembership(user_id=user.id, hospital_id=hospital_id, role=role))

    if "doctor" in roles:
        profile = db.session.scalar(select(DoctorProfile).where(
            DoctorProfile.user_id == user.id, DoctorProfile.hospital_id == hospital_id))
        if profile is None:
            db.session.add(DoctorProfile(
                user_id=user.id, hospital_id=hospital_id, department_id=department_id, title=title,
                specialty=specialty, default_consult_minutes=default_consult_minutes,
            ))
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise Conflict("The staff account could not be created because of conflicting details.") from None
    audit_service.record("staff.granted_roles", actor=actor, hospital_id=hospital_id, target_type="user",
                         target_id=user.id, details={"roles": sorted(roles)})
    db.session.commit()
    return user


def list_staff(hospital_id: uuid.UUID) -> list[dict]:
    get_hospital(hospital_id)
    stmt = (
        select(User, StaffMembership.role)
        .join(StaffMembership, StaffMembership.user_id == User.id)
        .where(StaffMembership.hospital_id == hospital_id, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    rows = db.session.execute(stmt).all()
    by_user: dict[uuid.UUID, dict] = {}
    for user, role in rows:
        if user.id not in by_user:
            doc_profile = db.session.scalar(
                select(DoctorProfile).where(DoctorProfile.user_id == user.id, DoctorProfile.hospital_id == hospital_id)
            )
            dept_name = None
            if doc_profile and doc_profile.department_id:
                dept = db.session.get(Department, doc_profile.department_id)
                dept_name = dept.name if dept else None

            by_user[user.id] = {
                "id": str(user.id),
                "full_name": user.full_name,
                "email": user.email,
                "phone": user.phone,
                "roles": [],
                "department_name": dept_name,
                "title": doc_profile.title if doc_profile else None,
                "created_at": user.created_at.isoformat() if user.created_at else "",
            }
        by_user[user.id]["roles"].append(role)
    return list(by_user.values())


def get_wallet(hospital_id: uuid.UUID) -> dict:
    hospital = get_hospital(hospital_id)
    settings = dict(hospital.settings or {})
    tokens = int(settings.get("wallet_tokens", 2500))
    low_thresh = int(settings.get("low_balance_threshold", 200))
    ledger = list(settings.get("ledger", []))
    return {
        "hospital_id": hospital.id,
        "wallet_tokens": tokens,
        "usd_value": round(tokens * 0.01, 2),
        "low_balance_threshold": low_thresh,
        "rates": {"sms_tokens": 5, "email_tokens": 1, "push_tokens": 0},
        "ledger": ledger[-50:],
    }


def topup_wallet(
    actor: Actor,
    hospital_id: uuid.UUID,
    *,
    tokens: int,
    payment_method: str = "card_verified",
    description: str | None = None,
) -> dict:
    hospital = get_hospital(hospital_id)
    require(actor, Perm.STAFF_MANAGE, hospital_id)
    if tokens < 100:
        raise ValidationFailed(details={"tokens": ["Minimum top-up is 100 tokens."]})

    settings = dict(hospital.settings or {})
    current_tokens = int(settings.get("wallet_tokens", 0))
    new_balance = current_tokens + tokens
    settings["wallet_tokens"] = new_balance

    ledger = list(settings.get("ledger", []))
    now = utcnow()
    ledger_entry = {
        "id": f"tx_topup_{int(now.timestamp())}_{secrets.token_hex(3)}",
        "timestamp": now.isoformat(),
        "type": "topup",
        "amount": tokens,
        "description": description or f"Token Top-Up via {payment_method}",
        "balance_after": new_balance,
    }
    ledger.append(ledger_entry)
    settings["ledger"] = ledger
    hospital.settings = settings
    db.session.add(hospital)

    audit_service.record(
        "hospital.wallet_topup",
        actor=actor,
        hospital_id=hospital_id,
        target_type="hospital",
        target_id=hospital.id,
        details={"tokens_purchased": tokens, "new_balance": new_balance, "payment_method": payment_method},
    )
    db.session.commit()
    return get_wallet(hospital_id)
