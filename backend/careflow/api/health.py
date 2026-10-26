from flask import Blueprint, current_app, jsonify
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from careflow.extensions import db

bp = Blueprint("health", __name__, url_prefix="/health")


@bp.get("/live")
def live():
    """Process is up."""
    return jsonify(status="ok")


@bp.get("/ready")
def ready():
    """Process can serve traffic: the database is reachable."""
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        current_app.logger.exception("readiness check failed")
        return jsonify(status="unavailable", database="unreachable"), 503
    return jsonify(status="ok", database="ok")
