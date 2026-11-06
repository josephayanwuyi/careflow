import uuid

from flask_smorest import Blueprint

from careflow.schemas.scheduling import (
    ExceptionQueryArgs,
    ScheduleExceptionSchema,
    ScheduleTemplateSchema,
    SlotDaySchema,
    SlotQueryArgs,
)
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import scheduling_service

blp = Blueprint("schedules", __name__, url_prefix="/api/v1/hospitals", description="Doctor schedules (staff)")
slots_blp = Blueprint("slots", __name__, url_prefix="/api/v1/doctors", description="Bookable slots (public)")


@blp.route("/<uuid:hospital_id>/doctors/<uuid:doctor_id>/schedule-templates", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, ScheduleTemplateSchema(many=True))
@login_required
def list_templates(hospital_id: uuid.UUID, doctor_id: uuid.UUID):
    """A doctor's recurring weekly sessions (hospital staff)."""
    return scheduling_service.list_templates(current_actor(), hospital_id, doctor_id)


@blp.route("/<uuid:hospital_id>/doctors/<uuid:doctor_id>/schedule-templates", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ScheduleTemplateSchema)
@blp.response(201, ScheduleTemplateSchema)
@login_required
def create_template(data, hospital_id: uuid.UUID, doctor_id: uuid.UUID):
    """Add a weekly session. Requires `schedules.manage`. Overlapping sessions are rejected (409)."""
    return scheduling_service.create_template(current_actor(), hospital_id, doctor_id, **data)


@blp.route("/<uuid:hospital_id>/schedule-templates/<uuid:template_id>", methods=["DELETE"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(204)
@login_required
def deactivate_template(hospital_id: uuid.UUID, template_id: uuid.UUID):
    """Stop offering a weekly session. Existing appointments are not affected."""
    scheduling_service.deactivate_template(current_actor(), hospital_id, template_id)


@blp.route("/<uuid:hospital_id>/doctors/<uuid:doctor_id>/schedule-exceptions", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ExceptionQueryArgs, location="query")
@blp.response(200, ScheduleExceptionSchema(many=True))
@login_required
def list_exceptions(args, hospital_id: uuid.UUID, doctor_id: uuid.UUID):
    return scheduling_service.list_exceptions(current_actor(), hospital_id, doctor_id, args["date_from"])


@blp.route("/<uuid:hospital_id>/doctors/<uuid:doctor_id>/schedule-exceptions", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(ScheduleExceptionSchema)
@blp.response(201, ScheduleExceptionSchema)
@login_required
def create_exception(data, hospital_id: uuid.UUID, doctor_id: uuid.UUID):
    """Block time (leave, meetings) or add an extra session on one date. Requires `schedules.manage`.

    Blocking time does not cancel appointments already approved in it; staff must reschedule those.
    """
    return scheduling_service.create_exception(current_actor(), hospital_id, doctor_id, **data)


@blp.route("/<uuid:hospital_id>/schedule-exceptions/<uuid:exception_id>", methods=["DELETE"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(204)
@login_required
def delete_exception(hospital_id: uuid.UUID, exception_id: uuid.UUID):
    scheduling_service.delete_exception(current_actor(), hospital_id, exception_id)


@slots_blp.route("/<uuid:doctor_id>/slots", methods=["GET"])
@slots_blp.arguments(SlotQueryArgs, location="query")
@slots_blp.response(200, SlotDaySchema)
def get_slots(args, doctor_id: uuid.UUID):
    """Slots on a hospital-local date. `available` is false when taken or outside the booking window.
    Never reveals who holds a slot."""
    doctor, slots = scheduling_service.get_public_slots(doctor_id, args["date"])
    return {"doctor_id": doctor.id, "date": args["date"], "timezone": doctor.hospital.timezone,
            "slots": slots}
