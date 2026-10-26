from flask import request
from flask_smorest import Blueprint

from careflow.extensions import db, limiter
from careflow.models import DoctorProfile, Hospital
from careflow.schemas.auth import ChangePasswordSchema, LoginSchema, MeSchema, RefreshSchema, RegisterPatientSchema, TokenSchema
from careflow.security.auth import BEARER_SECURITY, build_actor, current_actor, login_required
from careflow.security.policy import Actor
from careflow.services import auth_service

blp = Blueprint("auth", __name__, url_prefix="/api/v1/auth", description="Registration, sign-in and sessions")


def me_view(actor: Actor) -> dict:
    memberships = []
    for hospital_id, roles in sorted(actor.roles_by_hospital.items(), key=lambda kv: str(kv[0])):
        hospital = db.session.get(Hospital, hospital_id)
        doctor_id = None
        if "doctor" in roles:
            doctor_id = db.session.query(DoctorProfile.id).filter_by(
                user_id=actor.user_id, hospital_id=hospital_id).scalar()
        memberships.append({
            "hospital_id": hospital_id,
            "hospital_name": hospital.name,
            "roles": sorted(roles),
            "permissions": sorted(actor.permissions_in(hospital_id)),
            "doctor_id": doctor_id,
        })
    user = actor.user
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "phone": user.phone,
            "patient_id": actor.patient_id, "memberships": memberships}


def _token_view(pair: auth_service.TokenPair) -> dict:
    actor = build_actor(pair.user, pair.session_id)
    return {"access_token": pair.access_token, "refresh_token": pair.refresh_token,
            "token_type": "Bearer", "expires_in": pair.expires_in, "user": me_view(actor)}


@blp.route("/register", methods=["POST"])
@limiter.limit("5/minute")
@blp.arguments(RegisterPatientSchema)
@blp.response(201, MeSchema)
def register(data):
    """Create a patient account."""
    user, patient = auth_service.register_patient(**data)
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "phone": user.phone,
            "patient_id": patient.id, "memberships": []}


@blp.route("/login", methods=["POST"])
@limiter.limit("10/minute")
@blp.arguments(LoginSchema)
@blp.response(200, TokenSchema)
def login(data):
    """Sign in. Returns a short-lived access token and a single-use refresh token."""
    pair = auth_service.login(email=data["email"], password=data["password"], client_type=data["client_type"],
                              user_agent=request.headers.get("User-Agent"))
    return _token_view(pair)


@blp.route("/refresh", methods=["POST"])
@limiter.limit("30/minute")
@blp.arguments(RefreshSchema)
@blp.response(200, TokenSchema)
def refresh(data):
    """Exchange a refresh token for a new token pair. Each refresh token works once."""
    return _token_view(auth_service.refresh(data["refresh_token"]))


@blp.route("/logout", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(204)
@login_required
def logout():
    """End the current session. Its access and refresh tokens stop working immediately."""
    actor = current_actor()
    auth_service.logout(actor.session_id, actor.user_id)


@blp.route("/me", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, MeSchema)
@login_required
def me():
    """The signed-in user, their hospital roles and effective permissions."""
    return me_view(current_actor())


@blp.route("/change-password", methods=["POST"])
@limiter.limit("5/minute")
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ChangePasswordSchema)
@blp.response(204)
@login_required
def change_password(data):
    """Change password. All other sessions of this user are ended."""
    actor = current_actor()
    auth_service.change_password(user=actor.user, session_id=actor.session_id,
                                 current_password=data["current_password"], new_password=data["new_password"])
