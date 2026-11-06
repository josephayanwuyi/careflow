"""Registration, login, token refresh and logout."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from flask import current_app
from flask_jwt_extended import create_access_token
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from careflow.errors import Conflict, Unauthenticated, ValidationFailed
from careflow.extensions import db
from careflow.models import AuthSession, Hospital, PatientProfile, RefreshToken, StaffMembership, User
from careflow.models.base import utcnow
from careflow.security.passwords import hash_password, needs_rehash, verify_password
from careflow.services import audit_service
from careflow.services.normalize import normalize_email, normalize_phone

INVALID_CREDENTIALS = "The email or password is incorrect, or the account is temporarily locked."


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User
    session_id: uuid.UUID


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_patient(
    *, email: str, password: str, full_name: str, phone: str | None, date_of_birth: date | None
) -> tuple[User, PatientProfile]:
    email = normalize_email(email)
    phone = normalize_phone(phone) if phone else None
    if db.session.scalar(select(User.id).where(User.email == email)):
        raise Conflict("An account with this email already exists.", code="email_taken")
    if phone and db.session.scalar(select(User.id).where(User.phone == phone)):
        raise Conflict("An account with this phone number already exists.", code="phone_taken")

    user = User(email=email, phone=phone, full_name=full_name.strip(), password_hash=hash_password(password),
                password_changed_at=utcnow())
    db.session.add(user)
    db.session.flush()
    patient = PatientProfile(user_id=user.id, full_name=user.full_name, phone=phone, date_of_birth=date_of_birth)
    db.session.add(patient)
    db.session.flush()
    audit_service.record("auth.patient_registered", actor_user_id=user.id, target_type="patient", target_id=patient.id)
    try:
        db.session.commit()
    except IntegrityError:
        # Lost a race with a concurrent registration using the same email/phone.
        db.session.rollback()
        raise Conflict("An account with these details already exists.", code="account_exists") from None
    return user, patient


def register_hospital_admin(
    *,
    hospital_name: str,
    hospital_code: str,
    hospital_slug: str | None = None,
    timezone: str = "Africa/Lagos",
    address: str | None = None,
    admin_name: str,
    admin_email: str,
    admin_password: str,
    phone: str | None = None,
    starter_tokens: int = 2500,
) -> tuple[Hospital, User]:
    email = normalize_email(admin_email)
    phone = normalize_phone(phone) if phone else None
    code = hospital_code.strip().upper()
    if not hospital_slug:
        hospital_slug = re.sub(r"[^a-z0-9-]", "", code.lower())
    else:
        hospital_slug = hospital_slug.strip().lower()

    if db.session.scalar(select(Hospital.id).where(Hospital.code == code)):
        raise Conflict(f"A hospital with code '{code}' already exists.", code="hospital_code_taken")
    if db.session.scalar(select(Hospital.id).where(Hospital.slug == hospital_slug)):
        raise Conflict(f"A hospital with slug '{hospital_slug}' already exists.", code="hospital_slug_taken")
    if db.session.scalar(select(User.id).where(User.email == email)):
        raise Conflict("An account with this email already exists.", code="email_taken")

    now = utcnow()
    hospital = Hospital(
        code=code,
        name=hospital_name.strip(),
        slug=hospital_slug,
        timezone=timezone or "Africa/Lagos",
        address=address.strip() if address else None,
        settings={
            "wallet_tokens": starter_tokens,
            "low_balance_threshold": 200,
            "ledger": [
                {
                    "id": f"tx_init_{int(now.timestamp())}",
                    "timestamp": now.isoformat(),
                    "type": "starter_credit",
                    "amount": starter_tokens,
                    "description": "Starter Communication Token Allocation",
                    "balance_after": starter_tokens,
                }
            ],
        },
    )
    db.session.add(hospital)
    db.session.flush()

    user = User(
        email=email,
        phone=phone,
        full_name=admin_name.strip(),
        password_hash=hash_password(admin_password),
        password_changed_at=now,
    )
    db.session.add(user)
    db.session.flush()

    membership = StaffMembership(
        user_id=user.id,
        hospital_id=hospital.id,
        role="hospital_admin",
    )
    db.session.add(membership)
    db.session.flush()

    audit_service.record(
        "auth.hospital_admin_registered",
        actor_user_id=user.id,
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise Conflict("An account or hospital with these details already exists.", code="registration_conflict") from None
    return hospital, user


def _issue_tokens(user: User, session: AuthSession) -> TokenPair:
    raw_refresh = secrets.token_urlsafe(48)
    db.session.add(RefreshToken(session_id=session.id, token_hash=_hash_token(raw_refresh), expires_at=session.expires_at))
    access = create_access_token(identity=str(user.id), additional_claims={"sid": str(session.id)})
    expires = current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]
    return TokenPair(access_token=access, refresh_token=raw_refresh, expires_in=int(expires.total_seconds()),
                     user=user, session_id=session.id)


def login(*, email: str, password: str, client_type: str | None, user_agent: str | None) -> TokenPair:
    cfg = current_app.config
    now = utcnow()
    user = db.session.scalar(select(User).where(User.email == normalize_email(email)).with_for_update())

    if user is not None and user.locked_until and user.locked_until > now:
        verify_password(None, password)  # keep timing similar
        audit_service.record("auth.login_blocked_locked", actor_user_id=user.id)
        db.session.commit()
        raise Unauthenticated(INVALID_CREDENTIALS, code="invalid_credentials")

    # Always run the hash check so unknown emails take about as long as wrong passwords.
    password_ok = verify_password(user.password_hash if user else None, password)
    if user is None or not user.is_active or not password_ok:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= cfg["LOGIN_MAX_FAILURES"]:
                user.locked_until = now + timedelta(minutes=cfg["LOGIN_LOCKOUT_MINUTES"])
                user.failed_login_count = 0
                audit_service.record("auth.account_locked", actor_user_id=user.id)
            audit_service.record("auth.login_failed", actor_user_id=user.id)
            db.session.commit()
        raise Unauthenticated(INVALID_CREDENTIALS, code="invalid_credentials")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    session = AuthSession(
        user_id=user.id,
        expires_at=now + cfg["SESSION_MAX_AGE"],
        last_used_at=now,
        client_type=(client_type or "")[:32] or None,
        user_agent=(user_agent or "")[:256] or None,
    )
    db.session.add(session)
    db.session.flush()
    tokens = _issue_tokens(user, session)
    audit_service.record("auth.login", actor_user_id=user.id, target_type="session", target_id=session.id)
    db.session.commit()
    return tokens


def refresh(raw_token: str) -> TokenPair:
    """Rotate a refresh token. Re-use of an already-used token revokes the whole session."""
    now = utcnow()
    token = db.session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw_token)).with_for_update()
    )
    if token is None:
        raise Unauthenticated("Your session has ended. Please sign in again.", code="session_ended")

    session = db.session.get(AuthSession, token.session_id, with_for_update=True)
    user = db.session.get(User, session.user_id)

    if token.used_at is not None:
        if session.revoked_at is None:
            session.revoked_at = now
            session.revoked_reason = "refresh_token_reuse"
            audit_service.record("auth.refresh_token_reuse_detected", actor_user_id=session.user_id,
                                 target_type="session", target_id=session.id)
            db.session.commit()
        raise Unauthenticated("Your session has ended. Please sign in again.", code="session_ended")

    if session.revoked_at is not None or session.expires_at <= now or token.expires_at <= now or not user.is_active:
        raise Unauthenticated("Your session has ended. Please sign in again.", code="session_ended")

    token.used_at = now
    session.last_used_at = now
    tokens = _issue_tokens(user, session)
    db.session.commit()
    return tokens


def logout(session_id: uuid.UUID, user_id: uuid.UUID) -> None:
    session = db.session.get(AuthSession, session_id)
    if session is not None and session.user_id == user_id and session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoked_reason = "logout"
        audit_service.record("auth.logout", actor_user_id=user_id, target_type="session", target_id=session_id)
        db.session.commit()


def change_password(*, user: User, session_id: uuid.UUID, current_password: str, new_password: str) -> None:
    """Change password and end every other session of this user."""
    if not verify_password(user.password_hash, current_password):
        raise Unauthenticated("The current password is incorrect.", code="invalid_credentials")
    if new_password.strip().lower() == user.email:
        raise ValidationFailed(details={"new_password": ["Password must not be the same as the email address."]})
    now = utcnow()
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    for session in db.session.scalars(select(AuthSession).where(
            AuthSession.user_id == user.id, AuthSession.id != session_id, AuthSession.revoked_at.is_(None))):
        session.revoked_at = now
        session.revoked_reason = "password_changed"
    audit_service.record("auth.password_changed", actor_user_id=user.id, target_type="user", target_id=user.id)
    db.session.commit()
