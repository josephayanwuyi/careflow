import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from careflow.extensions import db
from careflow.models import AuditEvent
from careflow.services import audit_service
from tests.conftest import auth_headers


def _walk_in(client, hid, headers, name, phone):
    resp = client.post(f"/api/v1/hospitals/{hid}/patients", json={"full_name": name, "phone": phone}, headers=headers)
    assert resp.status_code == 201
    return resp.get_json()


def test_sensitive_actions_are_audited(client, factory):
    hid = factory.hospital()
    rec = factory.staff(hid, "receptionist")
    headers = auth_headers(client, rec["email"])
    patient = _walk_in(client, hid, headers, "Audit Subject", "08030000010")
    events = client.get(f"/api/v1/hospitals/{hid}/audit-events", headers=headers).get_json()["items"]
    reg = next(e for e in events if e["action"] == "patient.registered_walk_in")
    assert reg["target_id"] == patient["id"]
    assert reg["actor_user_id"] == str(rec["user_id"])
    assert reg["actor_roles"] == "receptionist"
    assert reg["request_id"]
    # Audit details never contain the patient's name or phone.
    assert "Audit Subject" not in str(events) and "8030000010" not in str(events)


def test_audit_filters_and_pagination(client, factory):
    hid = factory.hospital()
    headers = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    for i in range(5):
        _walk_in(client, hid, headers, f"Paged Patient {i}", f"0803000002{i}")
    seen, cursor = [], None
    while True:
        params = {"action": "patient.registered_walk_in", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get(f"/api/v1/hospitals/{hid}/audit-events", query_string=params, headers=headers).get_json()
        seen += [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5


def test_invalid_cursor_is_422(client, factory):
    hid = factory.hospital()
    headers = auth_headers(client, factory.staff(hid, "receptionist")["email"])
    resp = client.get(f"/api/v1/hospitals/{hid}/audit-events?cursor=garbage", headers=headers)
    assert resp.status_code == 422


def test_doctor_and_other_hospitals_cannot_read_audit(client, factory):
    a, b = factory.hospital("HOSA"), factory.hospital("HOSB")
    doctor = factory.staff(a, "doctor")
    rec_b = factory.staff(b, "receptionist")
    assert client.get(f"/api/v1/hospitals/{a}/audit-events", headers=auth_headers(client, doctor["email"])).status_code == 403
    assert client.get(f"/api/v1/hospitals/{a}/audit-events", headers=auth_headers(client, rec_b["email"])).status_code == 403


def test_audit_log_is_append_only_in_database(ctx, factory):
    hid = factory.hospital()
    event = audit_service.record("test.event", hospital_id=hid)
    db.session.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.session.execute(text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"), {"id": event.id})
    db.session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.session.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": event.id})
    db.session.rollback()
    assert db.session.get(AuditEvent, event.id).action == "test.event"


def test_ip_is_truncated():
    assert audit_service._ip_prefix("203.0.113.77") == "203.0.113.0/24"
    assert audit_service._ip_prefix("2001:db8:abcd:12::1") == "2001:db8:abcd::/48"
    assert audit_service._ip_prefix("nonsense") is None
