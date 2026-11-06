import uuid

from flask_smorest import Blueprint

from careflow.schemas.queue import (
    EntryListArgs,
    OptionalReasonSchema,
    PauseSchema,
    PrioritySchema,
    QueueEntrySchema,
    QueueEventSchema,
    QueueListArgs,
    QueueSummarySchema,
    TransferSchema,
    VersionSchema,
    WalkInEntrySchema,
    entry_view,
    queue_event_view,
    queue_summary_view,
)
from careflow.security.auth import BEARER_SECURITY, current_actor, login_required
from careflow.services import queue_service as svc

blp = Blueprint("queue", __name__, url_prefix="/api/v1", description="Live queues")


def _entry(entry_id: uuid.UUID) -> dict:
    return entry_view(svc.get_entry(current_actor(), entry_id))


# --- Joining ---------------------------------------------------------------------------------


@blp.route("/appointments/<uuid:appointment_id>/check-in", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(201, QueueEntrySchema)
@login_required
def check_in(appointment_id: uuid.UUID):
    """Check in an approved appointment (patient within the check-in window, or staff) and join the queue."""
    return _entry(svc.check_in(current_actor(), appointment_id).id)


@blp.route("/hospitals/<uuid:hospital_id>/queue/walk-ins", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(WalkInEntrySchema)
@blp.response(201, QueueEntrySchema)
@login_required
def add_walk_in(data, hospital_id: uuid.UUID):
    """Add a registered patient without an appointment to a doctor's queue today (staff)."""
    return _entry(svc.add_walk_in(current_actor(), hospital_id, **data).id)


# --- Reading ---------------------------------------------------------------------------------


@blp.route("/hospitals/<uuid:hospital_id>/queues", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(QueueListArgs, location="query")
@blp.response(200, QueueSummarySchema(many=True))
@login_required
def list_queues(args, hospital_id: uuid.UUID):
    """Queues for a day with counts by status. Doctors only see their own."""
    summaries = svc.list_queues(current_actor(), hospital_id, service_date=args["date"], doctor_id=args["doctor_id"],
                                department_id=args["department_id"])
    return [queue_summary_view(s) for s in summaries]


@blp.route("/queues/<uuid:queue_id>/entries", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(EntryListArgs, location="query")
@blp.response(200, QueueEntrySchema(many=True))
@login_required
def list_entries(args, queue_id: uuid.UUID):
    """Entries with live positions and wait estimates: with the doctor first, then waiting in call order."""
    return [entry_view(v) for v in svc.list_entries(current_actor(), queue_id, include_finished=args["include_finished"])]


@blp.route("/queues/<uuid:queue_id>/events", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, QueueEventSchema(many=True))
@login_required
def list_queue_events(queue_id: uuid.UUID):
    """Most recent 200 events for a queue (staff and the queue's doctor)."""
    return [queue_event_view(e, "staff") for e in svc.queue_events(current_actor(), queue_id)]


@blp.route("/queue/me", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, QueueEntrySchema(many=True))
@login_required
def my_queue():
    """The signed-in patient's live queue places with position and estimated wait."""
    return [entry_view(v) for v in svc.my_active_entries(current_actor())]


@blp.route("/queue-entries/<uuid:entry_id>", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, QueueEntrySchema)
@login_required
def get_entry(entry_id: uuid.UUID):
    return _entry(entry_id)


@blp.route("/queue-entries/<uuid:entry_id>/events", methods=["GET"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, QueueEventSchema(many=True))
@login_required
def get_entry_events(entry_id: uuid.UUID):
    events, kind = svc.entry_events(current_actor(), entry_id)
    return [queue_event_view(e, kind) for e in events]


# --- Queue-level actions ---------------------------------------------------------------------


@blp.route("/queues/<uuid:queue_id>/call-next", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, QueueEntrySchema)
@login_required
def call_next(queue_id: uuid.UUID):
    """Call the next eligible patient. Refused (409) if someone is already with the doctor, the queue is
    paused, or the doctor is not available, so repeated taps can never call two patients."""
    return _entry(svc.call_next(current_actor(), queue_id).id)


@blp.route("/queues/<uuid:queue_id>/pause", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.arguments(PauseSchema)
@blp.response(200, QueueSummarySchema)
@login_required
def pause(data, queue_id: uuid.UUID):
    svc.set_paused(current_actor(), queue_id, paused=True, reason=data["reason"])
    return _summary(queue_id)


@blp.route("/queues/<uuid:queue_id>/resume", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, QueueSummarySchema)
@login_required
def resume(queue_id: uuid.UUID):
    svc.set_paused(current_actor(), queue_id, paused=False, reason=None)
    return _summary(queue_id)


def _summary(queue_id: uuid.UUID) -> dict:
    queue, _ = svc.get_queue(current_actor(), queue_id)
    [summary] = [s for s in svc.list_queues(current_actor(), queue.hospital_id, service_date=queue.service_date,
                                            doctor_id=queue.doctor_id, department_id=None)]
    return queue_summary_view(summary)


# --- Entry actions ---------------------------------------------------------------------------


def _entry_action(path: str, fn, schema=VersionSchema, doc: str = ""):
    """Register POST /queue-entries/<id>/<path> calling fn(actor, entry_id, **body)."""

    @blp.route(f"/queue-entries/<uuid:entry_id>/{path}", methods=["POST"], endpoint=f"entry_{path.replace('-', '_')}")
    @blp.doc(security=BEARER_SECURITY, description=doc)
    @blp.arguments(schema)
    @blp.response(200, QueueEntrySchema)
    @login_required
    def handler(data, entry_id: uuid.UUID):
        result = fn(current_actor(), entry_id, **data)
        return _entry(result.id)

    return handler


_entry_action("recall", svc.recall, doc="Call the same patient again.")
_entry_action("start", svc.start, doc="Start the consultation for the called patient.")
_entry_action("complete", svc.complete, doc="Complete the consultation. The appointment becomes completed.")
_entry_action("mark-missed", svc.mark_missed, OptionalReasonSchema,
              doc="The called patient did not respond.")
_entry_action("return", svc.return_to_queue, OptionalReasonSchema,
              doc="A patient who missed their call rejoins at the back of the queue (staff).")
_entry_action("cancel", svc.cancel, OptionalReasonSchema,
              doc="Leave the queue. Patients may cancel their own place; staff must give a reason.")
_entry_action("transfer", svc.transfer, TransferSchema,
              doc="Move to another doctor's queue today, keeping the patient's place in line (staff).")
_entry_action("priority", svc.set_priority, PrioritySchema,
              doc="Change priority (requires queue.priority.set). Clinical triage remains a staff decision.")


@blp.route("/queue-entries/<uuid:entry_id>/confirm-arrival", methods=["POST"])
@blp.doc(security=BEARER_SECURITY)
@blp.response(200, QueueEntrySchema)
@login_required
def confirm_arrival(entry_id: uuid.UUID):
    """Staff confirm a self-checked-in patient is physically present."""
    return _entry(svc.confirm_arrival(current_actor(), entry_id).id)
