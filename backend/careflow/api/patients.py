import uuid

from flask_smorest import Blueprint

from careflow.schemas.patient import (
    HospitalPatientSchema,
    MyProfileSchema,
    MyProfileUpdateSchema,
    PatientSearchArgs,
    WalkInRegistrationSchema,
    hospital_patient_view,
)
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import patient_service

blp = Blueprint("patients", __name__, url_prefix="/api/v1/patients", description="Patient self-service")
hospital_blp = Blueprint("hospital_patients", __name__, url_prefix="/api/v1/hospitals",
                         description="Patients as seen by hospital staff")


@blp.route("/me", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, MyProfileSchema)
@login_required
def get_me():
    return patient_service.get_my_profile(current_actor())


@blp.route("/me", methods=["PATCH"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(MyProfileUpdateSchema)
@blp.response(200, MyProfileSchema)
@login_required
def update_me(data):
    return patient_service.update_my_profile(current_actor(), **data)


@hospital_blp.route("/<uuid:hospital_id>/patients", methods=["POST"])
@hospital_blp.doc(security=BEARER_SECURITY)
@hospital_blp.arguments(WalkInRegistrationSchema)
@hospital_blp.response(201, HospitalPatientSchema)
@login_required
def register_walk_in(data, hospital_id: uuid.UUID):
    """Register a patient at the desk. Returns 409 `possible_duplicate` with candidates unless confirmed."""
    patient, record = patient_service.register_walk_in(current_actor(), hospital_id, **data)
    return hospital_patient_view(patient, record)


@hospital_blp.route("/<uuid:hospital_id>/patients", methods=["GET"])
@hospital_blp.doc(security=BEARER_SECURITY)
@hospital_blp.arguments(PatientSearchArgs, location="query")
@hospital_blp.response(200, HospitalPatientSchema(many=True))
@login_required
def search_patients(args, hospital_id: uuid.UUID):
    """Search this hospital's patients by hospital number, phone, or name (max 20 results)."""
    rows = patient_service.search_patients(current_actor(), hospital_id, args["q"])
    return [hospital_patient_view(p, r) for p, r in rows]


@hospital_blp.route("/<uuid:hospital_id>/patients/<uuid:patient_id>", methods=["GET"])
@hospital_blp.doc(security=BEARER_SECURITY)
@hospital_blp.response(200, HospitalPatientSchema)
@login_required
def get_patient(hospital_id: uuid.UUID, patient_id: uuid.UUID):
    patient, record = patient_service.get_patient_at_hospital(current_actor(), hospital_id, patient_id)
    return hospital_patient_view(patient, record)
