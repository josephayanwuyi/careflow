import re
import uuid

from flask import request
from flask_smorest import Blueprint

from careflow.errors import ValidationFailed
from careflow.extensions import limiter
from careflow.schemas.appointment import (
    AppointmentPageSchema,
    AppointmentRequestSchema,
    AppointmentSchema,
    HistoryEntrySchema,
    HospitalAppointmentsArgs,
    MyAppointmentsArgs,
    ProposeRescheduleSchema,
    ReasonOptionalSchema,
    ReasonRequiredSchema,
    StaffBookingSchema,
    VersionOnlySchema,
    appointment_view,
    history_view,
)
from careflow.schemas.common import decode_cursor, encode_cursor
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import appointment_service as svc

blp = Blueprint("appointments", __name__, url_prefix="/api/v1/appointments", description="Appointments")
hospital_blp = Blueprint("hospital_appointments", __name__, url_prefix="/api/v1/hospitals",
                         description="Appointments as seen by hospital staff and doctors")

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


def _view(appt, kind):
    return appointment_view(appt, kind, svc.hospital_patient_numbers([appt]) if kind != "patient" else None)


def _page(items, kind, limit):
    numbers = svc.hospital_patient_numbers(items) if kind != "patient" else None
    next_cursor = encode_cursor(items[-1].start_at, items[-1].id) if len(items) == limit else None
    return {"items": [appointment_view(a, kind, numbers) for a in items], "next_cursor": next_cursor}


# --- Patient -----------------------------------------------------------------------------


@blp.route("", methods=["POST"])
@limiter.limit("20/hour")
@blp.doc(security=BEARER_SECURITY, parameters=[{
    "in": "header", "name": "Idempotency-Key", "required": False, "schema": {"type": "string"},
    "description": "Retrying with the same key returns the original request instead of creating another."}])
@blp.arguments(AppointmentRequestSchema)
@blp.response(201, AppointmentSchema)
@blp.alt_response(200, schema=AppointmentSchema, description="Idempotent replay of an earlier request")
@login_required
def request_appointment(data):
    """Request an appointment (patients). The result is a *request* awaiting hospital approval."""
    key = request.headers.get("Idempotency-Key")
    if key is not None and not _IDEMPOTENCY_KEY.match(key):
        raise ValidationFailed(details={"Idempotency-Key": ["Use 8-64 letters, digits, '-' or '_'."]})
    result = svc.request_appointment(current_actor(), idempotency_key=key, **data)
    return _view(result.appointment, "patient"), (201 if result.created else 200)


@blp.route("/mine", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(MyAppointmentsArgs, location="query")
@blp.response(200, AppointmentPageSchema)
@login_required
def my_appointments(args):
    """The signed-in patient's appointments, newest first."""
    items = svc.list_mine(current_actor(), statuses=args["status"], limit=args["limit"],
                          cursor=decode_cursor(args["cursor"]))
    page = _page(items, "patient", args["limit"])
    return page


@blp.route("/<uuid:appointment_id>", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, AppointmentSchema)
@login_required
def get_appointment(appointment_id: uuid.UUID):
    """Visible to the patient, the hospital's reviewing staff, and the assigned doctor. Others get 404."""
    appt, kind = svc.get(current_actor(), appointment_id)
    return _view(appt, kind)


@blp.route("/<uuid:appointment_id>/history", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, HistoryEntrySchema(many=True))
@login_required
def get_history(appointment_id: uuid.UUID):
    """Every status change, oldest first."""
    entries, kind = svc.history(current_actor(), appointment_id)
    return [history_view(e, kind) for e in entries]


@blp.route("/<uuid:appointment_id>/accept-reschedule", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, AppointmentSchema)
@login_required
def accept_reschedule(appointment_id: uuid.UUID):
    """Patient accepts the time proposed by the hospital."""
    return _view(svc.accept_reschedule(current_actor(), appointment_id), "patient")


@blp.route("/<uuid:appointment_id>/decline-reschedule", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ReasonOptionalSchema)
@blp.response(200, AppointmentSchema)
@login_required
def decline_reschedule(data, appointment_id: uuid.UUID):
    """Patient declines the proposed time; the appointment is cancelled."""
    return _view(svc.decline_reschedule(current_actor(), appointment_id, reason=data["reason"]), "patient")


@blp.route("/<uuid:appointment_id>/cancel", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ReasonOptionalSchema)
@blp.response(200, AppointmentSchema)
@login_required
def cancel(data, appointment_id: uuid.UUID):
    """Cancel (patient or staff). Staff must give a reason."""
    actor = current_actor()
    appt = svc.cancel(actor, appointment_id, reason=data["reason"], expected_version=data["expected_version"])
    return _view(appt, svc.actor_kind_for(actor, appt))


# --- Staff review ------------------------------------------------------------------------


@blp.route("/<uuid:appointment_id>/approve", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(VersionOnlySchema)
@blp.response(200, AppointmentSchema)
@login_required
def approve(data, appointment_id: uuid.UUID):
    """Approve a request. Fails with 409 `slot_unavailable` if the time was taken meanwhile."""
    return _view(svc.approve(current_actor(), appointment_id, expected_version=data["expected_version"]), "staff")


@blp.route("/<uuid:appointment_id>/reject", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ReasonRequiredSchema)
@blp.response(200, AppointmentSchema)
@login_required
def reject(data, appointment_id: uuid.UUID):
    return _view(svc.reject(current_actor(), appointment_id, reason=data["reason"],
                            expected_version=data["expected_version"]), "staff")


@blp.route("/<uuid:appointment_id>/propose-reschedule", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ProposeRescheduleSchema)
@blp.response(200, AppointmentSchema)
@login_required
def propose_reschedule(data, appointment_id: uuid.UUID):
    """Offer a different time with the same doctor. The patient must accept or decline."""
    return _view(svc.propose_reschedule(current_actor(), appointment_id, new_start_at=data["start_at"],
                                        reason=data["reason"], expected_version=data["expected_version"]), "staff")


@blp.route("/<uuid:appointment_id>/mark-no-show", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ReasonOptionalSchema)
@blp.response(200, AppointmentSchema)
@login_required
def mark_no_show(data, appointment_id: uuid.UUID):
    """Record that an approved patient did not attend (allowed after the grace period)."""
    return _view(svc.mark_no_show(current_actor(), appointment_id, reason=data["reason"],
                                  expected_version=data["expected_version"]), "staff")


@hospital_blp.route("/<uuid:hospital_id>/appointments", methods=["GET"])
@hospital_blp.doc(security=BEARER_SECURITY)
@hospital_blp.arguments(HospitalAppointmentsArgs, location="query")
@hospital_blp.response(200, AppointmentPageSchema)
@login_required
def hospital_appointments(args, hospital_id: uuid.UUID):
    """Appointments at a hospital, earliest first. Doctors only see their own.

    For the review queue use `?status=requested`.
    """
    limit = args.pop("limit")
    cursor = decode_cursor(args.pop("cursor"))
    statuses = args.pop("status")
    items, kind = svc.list_for_hospital(current_actor(), hospital_id, statuses=statuses, limit=limit,
                                        cursor=cursor, **args)
    return _page(items, kind, limit)


@hospital_blp.route("/<uuid:hospital_id>/appointments", methods=["POST"])
@hospital_blp.doc(security=BEARER_SECURITY)
@hospital_blp.arguments(StaffBookingSchema)
@hospital_blp.response(201, AppointmentSchema)
@login_required
def staff_book(data, hospital_id: uuid.UUID):
    """Book on a patient's behalf (e.g. by phone). Created as approved."""
    appt = svc.staff_book(current_actor(), hospital_id, patient_id=data["patient_id"], doctor_id=data["doctor_id"],
                          start_at=data["start_at"], reason_for_visit=data["reason_for_visit"])
    return _view(appt, "staff")
