"""Request authentication: resolves the bearer token into an `Actor`."""

from __future__ import annotations

import uuid
from functools import wraps

from flask import g
from flask_jwt_extended import get_jwt, verify_jwt_in_request
from sqlalchemy import select

from careflow.errors import Unauthenticated, error_response
from careflow.extensions import db, jwt
from careflow.models import AuthSession, PatientProfile, StaffMembership, User
from careflow.models.base import utcnow
from careflow.security.policy import Actor

BEARER_SECURITY = [{"bearerAuth": []}]


def load_actor() -> Actor:
    """Validate the access token and its session, then load the caller from the database."""
    verify_jwt_in_request()
    claims = get_jwt()
    try:
        user_id = uuid.UUID(claims["sub"])
        session_id = uuid.UUID(claims["sid"])
    except (KeyError, ValueError, TypeError):
        raise Unauthenticated("The access token is invalid.", code="token_invalid") from None

    session = db.session.get(AuthSession, session_id)
    if (
        session is None
        or session.user_id != user_id
        or session.revoked_at is not None
        or session.expires_at <= utcnow()
    ):
        raise Unauthenticated("Your session has ended. Please sign in again.", code="session_ended")

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthenticated("Your session has ended. Please sign in again.", code="session_ended")
    return build_actor(user, session_id)


def build_actor(user: User, session_id: uuid.UUID) -> Actor:
    roles: dict[uuid.UUID, set[str]] = {}
    for hospital_id, role in db.session.execute(
        select(StaffMembership.hospital_id, StaffMembership.role).where(
            StaffMembership.user_id == user.id, StaffMembership.is_active.is_(True)
        )
    ):
        roles.setdefault(hospital_id, set()).add(role)

    patient_id = db.session.scalar(select(PatientProfile.id).where(PatientProfile.user_id == user.id))
    return Actor(user=user, session_id=session_id, roles_by_hospital=roles, patient_id=patient_id)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        g.actor = load_actor()
        return fn(*args, **kwargs)

    return wrapper


def current_actor() -> Actor:
    actor = g.get("actor")
    if actor is None:
        raise Unauthenticated()
    return actor


# --- flask-jwt-extended error responses in the uniform format -------------------------


@jwt.unauthorized_loader
def _missing_token(reason: str):
    return error_response(401, "token_missing", "Authentication is required.")


@jwt.invalid_token_loader
def _invalid_token(reason: str):
    return error_response(401, "token_invalid", "The access token is invalid.")


@jwt.expired_token_loader
def _expired_token(header, payload):
    return error_response(401, "token_expired", "The access token has expired. Refresh it or sign in again.")


@jwt.revoked_token_loader
def _revoked_token(header, payload):
    return error_response(401, "session_ended", "Your session has ended. Please sign in again.")
