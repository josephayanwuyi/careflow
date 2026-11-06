"""GET /api/v1/stream - Server-Sent Events for live updates.

Authenticate with the normal `Authorization: Bearer` header (browsers: use a fetch-based SSE
client; the desktop app sets the header directly). Tokens are never accepted in the URL.

Events are hints ("this changed"); clients re-fetch the resource. On `resync`, or after any
reconnect, clients should reload their current view because events are not replayed.
"""

from __future__ import annotations

import json
import queue
import time

from flask import Blueprint, Response, current_app, g, stream_with_context
from sqlalchemy import select

from careflow.extensions import db
from careflow.models import AuthSession, Queue, QueueEntry, QueueStatus
from careflow.models.base import utcnow
from careflow.realtime import Scope, broker
from careflow.security.auth import login_required
from careflow.security.policy import Actor, Perm

bp = Blueprint("stream", __name__, url_prefix="/api/v1")

HEARTBEAT_SECONDS = 15
SESSION_RECHECK_SECONDS = 60


def build_scope(actor: Actor) -> Scope:
    scope = Scope()
    for hospital_id in actor.roles_by_hospital:
        if actor.can(Perm.QUEUE_MANAGE, hospital_id) or actor.can(Perm.APPOINTMENTS_REVIEW, hospital_id):
            scope.staff_hospitals.add(str(hospital_id))
    scope.doctor_ids = {str(d) for d in actor.doctor_ids.values()}
    if actor.patient_id:
        scope.patient_id = str(actor.patient_id)
        for queue_id, doctor_id in db.session.execute(
                select(QueueEntry.queue_id, Queue.doctor_id).join(Queue, Queue.id == QueueEntry.queue_id)
                .where(QueueEntry.patient_id == actor.patient_id, QueueEntry.status.in_(QueueStatus.ACTIVE))):
            scope.patient_queue_ids.add(str(queue_id))
            scope.patient_doctor_ids.add(str(doctor_id))
    return scope


def _session_still_valid(session_id) -> bool:
    session = db.session.get(AuthSession, session_id, populate_existing=True)
    return session is not None and session.revoked_at is None and session.expires_at > utcnow()


@bp.get("/stream")
@login_required
def stream():
    actor: Actor = g.actor
    scope = build_scope(actor)
    session_id = actor.session_id
    db.session.remove()  # don't hold a pooled connection for the lifetime of the stream
    subscriber = broker.subscribe(scope)
    app = current_app._get_current_object()

    def generate():
        last_check = time.monotonic()
        try:
            yield "retry: 5000\n\n"
            yield f"event: ready\ndata: {json.dumps({'scope': 'ok'})}\n\n"
            while True:
                try:
                    message = subscriber.messages.get(timeout=HEARTBEAT_SECONDS)
                    yield f"id: {message['id']}\nevent: {message['type']}\ndata: {json.dumps(message)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
                if subscriber.overflowed:
                    subscriber.overflowed = False
                    yield "event: resync\ndata: {}\n\n"
                if time.monotonic() - last_check > SESSION_RECHECK_SECONDS:
                    last_check = time.monotonic()
                    with app.app_context():
                        valid = _session_still_valid(session_id)
                        db.session.remove()
                    if not valid:
                        yield "event: session_ended\ndata: {}\n\n"
                        return
        finally:
            broker.unsubscribe(subscriber)

    # No "Connection" header: hop-by-hop headers are forbidden for WSGI apps (PEP 3333).
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)
