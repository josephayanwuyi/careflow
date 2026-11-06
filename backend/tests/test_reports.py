import csv
import io
from datetime import UTC, datetime, time

import pytest

from tests.conftest import auth_headers, register_patient


@pytest.fixture
def clinic(factory):
    hid = factory.hospital("HOSA")
    dept = factory.department(hid)
    doctor = factory.staff(hid, "doctor", department_id=dept)
    factory.schedule(hid, doctor["doctor_id"], start=time(0, 0), end=time(23, 45))
    return {"hospital_id": hid, "doctor": doctor, "receptionist": factory.staff(hid, "receptionist"),
            "admin": factory.staff(hid, "hospital_admin")}


def _walk_in(client, clinic, headers, name):
    hid = clinic["hospital_id"]
    p = client.post(f"/api/v1/hospitals/{hid}/patients", json={"full_name": name}, headers=headers).get_json()
    return client.post(f"/api/v1/hospitals/{hid}/queue/walk-ins", headers=headers,
                       json={"patient_id": p["id"], "doctor_id": str(clinic["doctor"]["doctor_id"])}).get_json()


def test_dashboard_counts_and_alerts(client, factory, clinic):
    rec = auth_headers(client, clinic["receptionist"]["email"])
    hid = clinic["hospital_id"]
    e1 = _walk_in(client, clinic, rec, "First Person")
    _walk_in(client, clinic, rec, "Second Person")
    client.post(f"/api/v1/queue-entries/{e1['id']}/cancel", json={"reason": "Went home"}, headers=rec)
    p = register_patient(client)
    factory.approved_appointment(hid, clinic["doctor"]["doctor_id"], p["patient_id"],
                                 start_at=datetime.now(UTC).replace(second=0, microsecond=0))

    data = client.get(f"/api/v1/hospitals/{hid}/dashboard", headers=rec).get_json()
    assert data["queue"]["waiting"] == 1 and data["queue"]["cancelled"] == 1
    assert data["appointments"]["today"] >= 1
    assert sum(data["doctors"].values()) == 1
    assert data["alerts"][0]["event_type"] == "cancelled" and data["alerts"][0]["ticket_number"] == 1
    assert "First Person" not in str(data)


def test_dashboard_permissions(client, factory, clinic):
    doc = auth_headers(client, clinic["doctor"]["email"])
    assert client.get(f"/api/v1/hospitals/{clinic['hospital_id']}/dashboard", headers=doc).status_code == 403
    other = factory.clinic("HOSB")
    other_rec = auth_headers(client, other["receptionist"]["email"])
    assert client.get(f"/api/v1/hospitals/{clinic['hospital_id']}/dashboard", headers=other_rec).status_code == 403


def test_queue_day_csv(client, clinic):
    rec = auth_headers(client, clinic["receptionist"]["email"])
    hid = clinic["hospital_id"]
    entry = _walk_in(client, clinic, rec, "Csv Person")
    today = entry["service_date"]
    resp = client.get(f"/api/v1/hospitals/{hid}/reports/queue-day.csv", query_string={"date": today}, headers=rec)
    assert resp.status_code == 200 and resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    rows = list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))
    assert len(rows) == 1 and rows[0]["ticket"] == "1" and rows[0]["hospital_patient_number"] == "HOSA-000001"
    assert "Csv Person" not in resp.get_data(as_text=True)
    events = client.get(f"/api/v1/hospitals/{hid}/audit-events", query_string={"action": "report.exported"},
                        headers=rec).get_json()["items"]
    assert len(events) == 1


def test_exports_require_permission(client, factory, clinic):
    hid = clinic["hospital_id"]
    doc = auth_headers(client, clinic["doctor"]["email"])
    assert client.get(f"/api/v1/hospitals/{hid}/reports/queue-day.csv?date=2026-11-02", headers=doc).status_code == 403
    assert client.get(f"/api/v1/hospitals/{hid}/audit-events/export.csv", headers=doc).status_code == 403


def test_audit_csv(client, clinic):
    admin = auth_headers(client, clinic["admin"]["email"])
    hid = clinic["hospital_id"]
    client.post(f"/api/v1/hospitals/{hid}/departments", json={"name": "Radiology"}, headers=admin)
    resp = client.get(f"/api/v1/hospitals/{hid}/audit-events/export.csv", headers=admin)
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))
    assert any(r["action"] == "department.created" for r in rows)
