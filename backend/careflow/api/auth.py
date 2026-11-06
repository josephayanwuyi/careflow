import hmac
import secrets

from flask import after_this_request, current_app, request
from flask_smorest import Blueprint

from careflow.errors import Forbidden, Unauthenticated
from careflow.extensions import db, limiter
from careflow.models import Hospital
from careflow.schemas.auth import (
    ChangePasswordSchema,
    HospitalRegistrationResultSchema,
    LoginSchema,
    MeSchema,
    RefreshSchema,
    RegisterHospitalAdminSchema,
    RegisterPatientSchema,
    TokenSchema,
)
from careflow.security.auth import BEARER_SECURITY, build_actor, current_actor, login_required
from careflow.security.policy import Actor
from careflow.services import auth_service

blp = Blueprint("auth", __name__, url_prefix="/api/v1/auth", description="Registration, sign-in and sessions")


def me_view(actor: Actor) -> dict:
    memberships = []
    for hospital_id, roles in sorted(actor.roles_by_hospital.items(), key=lambda kv: str(kv[0])):
        hospital = db.session.get(Hospital, hospital_id)
        doctor_id = actor.doctor_ids.get(hospital_id)
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


# Browsers never see the refresh token: it lives in an HttpOnly cookie scoped to the auth endpoints.
# Using it requires the X-CSRF-Token header to match the (readable) CSRF cookie: double-submit protection.
REFRESH_COOKIE = "careflow_refresh"
CSRF_COOKIE = "careflow_csrf"
CSRF_HEADER = "X-CSRF-Token"
_COOKIE_PATH = "/api/v1/auth"


def _set_web_cookies(refresh_token: str) -> None:
    csrf = secrets.token_urlsafe(32)
    secure = current_app.config["COOKIE_SECURE"]
    max_age = int(current_app.config["SESSION_MAX_AGE"].total_seconds())

    @after_this_request
    def _set(response):
        response.set_cookie(REFRESH_COOKIE, refresh_token, max_age=max_age, path=_COOKIE_PATH, secure=secure,
                            httponly=True, samesite="Strict")
        response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, path="/", secure=secure, httponly=False,
                            samesite="Strict")
        return response


def _clear_web_cookies() -> None:
    @after_this_request
    def _clear(response):
        response.delete_cookie(REFRESH_COOKIE, path=_COOKIE_PATH)
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response


def _token_view(pair: auth_service.TokenPair, *, web: bool = False) -> dict:
    actor = build_actor(pair.user, pair.session_id)
    if web:
        _set_web_cookies(pair.refresh_token)
    return {"access_token": pair.access_token, "refresh_token": None if web else pair.refresh_token,
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


@blp.route("/register-hospital", methods=["POST"])
@limiter.limit("5/minute")
@blp.arguments(RegisterHospitalAdminSchema)
@blp.response(201, HospitalRegistrationResultSchema)
def register_hospital(data):
    """Register a new hospital organization and its primary administrator account."""
    client_type = data.pop("client_type", None)
    admin_password = data["admin_password"]
    admin_email = data["admin_email"]

    hospital, user = auth_service.register_hospital_admin(**data)

    pair = auth_service.login(
        email=admin_email,
        password=admin_password,
        client_type=client_type,
        user_agent=request.headers.get("User-Agent"),
    )
    token_data = _token_view(pair, web=client_type == "web")

    return {
        "hospital_id": hospital.id,
        "hospital_code": hospital.code,
        "hospital_name": hospital.name,
        "hospital_slug": hospital.slug,
        "user_id": user.id,
        "admin_email": user.email,
        "admin_name": user.full_name,
        "starter_tokens": hospital.settings.get("wallet_tokens", 2500),
        "token": token_data,
    }


@blp.route("/login", methods=["POST"])
@limiter.limit("10/minute")
@blp.arguments(LoginSchema)
@blp.response(200, TokenSchema)
def login(data):
    """Sign in. Returns a short-lived access token and a single-use refresh token.

    With `client_type: "web"` the refresh token is set as an HttpOnly cookie instead of being returned.
    """
    pair = auth_service.login(email=data["email"], password=data["password"], client_type=data["client_type"],
                              user_agent=request.headers.get("User-Agent"))
    return _token_view(pair, web=data["client_type"] == "web")


@blp.route("/refresh", methods=["POST"])
@limiter.limit("30/minute")
@blp.arguments(RefreshSchema)
@blp.response(200, TokenSchema)
def refresh(data):
    """Exchange a refresh token for a new token pair. Each refresh token works once.

    Browsers send no body token: the refresh cookie is used, and the X-CSRF-Token header must match the
    careflow_csrf cookie.
    """
    if data["refresh_token"]:
        return _token_view(auth_service.refresh(data["refresh_token"]))
    cookie_token = request.cookies.get(REFRESH_COOKIE)
    if not cookie_token:
        raise Unauthenticated("Your session has ended. Please sign in again.", code="session_ended")
    csrf_cookie, csrf_header = request.cookies.get(CSRF_COOKIE, ""), request.headers.get(CSRF_HEADER, "")
    if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise Forbidden("The request could not be verified. Reload the page and sign in again.", code="csrf_failed")
    try:
        pair = auth_service.refresh(cookie_token)
    except Unauthenticated:
        _clear_web_cookies()
        raise
    return _token_view(pair, web=True)


@blp.route("/logout", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(204)
@login_required
def logout():
    """End the current session. Its access and refresh tokens stop working immediately."""
    actor = current_actor()
    auth_service.logout(actor.session_id, actor.user_id)
    _clear_web_cookies()


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
