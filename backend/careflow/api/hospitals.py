import uuid

from flask_smorest import Blueprint

from careflow.schemas.hospital import (
    DepartmentSchema,
    DoctorPublicSchema,
    DoctorQuerySchema,
    HospitalSchema,
    NotificationReportSchema,
    StaffCreatedSchema,
    StaffCreateSchema,
    StaffItemSchema,
    WalletSchema,
    WalletTopUpSchema,
)
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import hospital_service, notification_service

blp = Blueprint("hospitals", __name__, url_prefix="/api/v1/hospitals",
                description="Hospitals, departments, doctors and staff")
doctors_blp = Blueprint("doctors", __name__, url_prefix="/api/v1/doctors", description="Doctor directory")


@blp.route("", methods=["GET"])
@blp.response(200, HospitalSchema(many=True))
def list_hospitals():
    """Active hospitals (public)."""
    return hospital_service.list_hospitals()


@blp.route("/<uuid:hospital_id>", methods=["GET"])
@blp.response(200, HospitalSchema)
def get_hospital(hospital_id: uuid.UUID):
    return hospital_service.get_hospital(hospital_id)


@blp.route("/<uuid:hospital_id>/departments", methods=["GET"])
@blp.response(200, DepartmentSchema(many=True))
def list_departments(hospital_id: uuid.UUID):
    """Active departments of a hospital (public)."""
    return hospital_service.list_departments(hospital_id)


@blp.route("/<uuid:hospital_id>/departments", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(DepartmentSchema)
@blp.response(201, DepartmentSchema)
@login_required
def create_department(data, hospital_id: uuid.UUID):
    """Create a department. Requires `departments.manage`."""
    return hospital_service.create_department(current_actor(), hospital_id, **data)


@blp.route("/<uuid:hospital_id>/doctors", methods=["GET"])
@blp.arguments(DoctorQuerySchema, location="query")
@blp.response(200, DoctorPublicSchema(many=True))
def list_doctors(args, hospital_id: uuid.UUID):
    """Doctors at a hospital, optionally filtered by department (public)."""
    return hospital_service.list_doctors(hospital_id, args["department_id"])


@blp.route("/<uuid:hospital_id>/staff", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(StaffCreateSchema)
@blp.response(201, StaffCreatedSchema)
@login_required
def create_staff(data, hospital_id: uuid.UUID):
    """Create a staff account or grant roles to an existing account. Requires `staff.manage`."""
    user = hospital_service.create_staff(current_actor(), hospital_id, **data)
    return {"user_id": user.id, "email": user.email, "roles": sorted(set(data["roles"]))}


@blp.route("/<uuid:hospital_id>/wallet", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, WalletSchema)
@login_required
def get_hospital_wallet(hospital_id: uuid.UUID):
    """Get the hospital communication wallet balance and ledger. Requires staff login."""
    return hospital_service.get_wallet(hospital_id)


@blp.route("/<uuid:hospital_id>/wallet/topup", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(WalletTopUpSchema)
@blp.response(200, WalletSchema)
@login_required
def topup_hospital_wallet(data, hospital_id: uuid.UUID):
    """Top up communication tokens for a hospital. Requires `staff.manage` (hospital_admin)."""
    return hospital_service.topup_wallet(current_actor(), hospital_id, **data)


@blp.route("/<uuid:hospital_id>/staff", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, StaffItemSchema(many=True))
@login_required
def list_staff(hospital_id: uuid.UUID):
    """List staff accounts of a hospital. Requires staff login."""
    return hospital_service.list_staff(hospital_id)


@blp.route("/<uuid:hospital_id>/notifications/report", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, NotificationReportSchema)
@login_required
def get_notification_report(hospital_id: uuid.UUID):
    """Notification dispatch statistics and audit report."""
    return notification_service.get_notification_report(hospital_id)


@doctors_blp.route("/<uuid:doctor_id>", methods=["GET"])
@doctors_blp.response(200, DoctorPublicSchema)
def get_doctor(doctor_id: uuid.UUID):
    return hospital_service.get_doctor(doctor_id)
