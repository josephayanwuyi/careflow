import uuid

from flask_smorest import Blueprint

from careflow.schemas.availability import (
    DoctorAvailabilitySchema,
    OwnStatusQuery,
    OwnStatusSchema,
    PublicAvailabilitySchema,
    StatusEventSchema,
    VerifyArrivalSchema,
    availability_view,
)
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import availability_service as svc
from careflow.services.hospital_service import get_doctor

blp = Blueprint("availability", __name__, url_prefix="/api/v1", description="Doctor availability")


@blp.route("/doctors/me/status", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(OwnStatusQuery, location="query")
@blp.response(200, DoctorAvailabilitySchema)
@login_required
def get_own_status(args):
    """The signed-in doctor's own current availability."""
    return availability_view(svc.get_own(current_actor(), args["hospital_id"]))


@blp.route("/doctors/me/status", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(OwnStatusSchema)
@blp.response(200, DoctorAvailabilitySchema)
@login_required
def set_own_status(data):
    """Doctor reports arrival (`checked_in`), availability, breaks, or departure (`checked_out`)."""
    return availability_view(svc.set_own_status(current_actor(), **data))


@blp.route("/hospitals/<uuid:hospital_id>/doctors/<uuid:doctor_id>/verify-arrival", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(VerifyArrivalSchema)
@blp.response(200, DoctorAvailabilitySchema)
@login_required
def verify_arrival(data, hospital_id: uuid.UUID, doctor_id: uuid.UUID):
    """Receptionist confirms the doctor is present (recorded as `staff_verified`)."""
    return availability_view(svc.verify_arrival(current_actor(), hospital_id, doctor_id, data["note"]))


@blp.route("/hospitals/<uuid:hospital_id>/doctors/availability", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, DoctorAvailabilitySchema(many=True))
@login_required
def hospital_availability(hospital_id: uuid.UUID):
    """All doctors' current status with reported vs. verified information (hospital staff)."""
    return sorted((availability_view(d) for d in svc.list_for_hospital(current_actor(), hospital_id)),
                  key=lambda v: v["name"])


@blp.route("/doctors/<uuid:doctor_id>/availability", methods=["GET"])
@blp.response(200, PublicAvailabilitySchema)
def public_availability(doctor_id: uuid.UUID):
    """Patient-facing status label only. Never includes location or personal details."""
    doctor = get_doctor(doctor_id)
    return {"doctor_id": doctor.id, "public_status": svc.public_status(doctor)}


@blp.route("/doctors/<uuid:doctor_id>/status-history", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, StatusEventSchema(many=True))
@login_required
def status_history(doctor_id: uuid.UUID):
    """Most recent 100 status changes (the doctor themself, or non-doctor staff of the hospital)."""
    return svc.history(current_actor(), doctor_id)
