"""GUI test fixtures. Tests run headless (offscreen) against an in-memory fake API client."""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from careflow_desktop.api.errors import ApiError, NetworkError, SessionExpired  # noqa: E402
from careflow_desktop.config import Config  # noqa: E402
from careflow_desktop.state import AppState, Hospital  # noqa: E402

HID = "h-1"
TZ = "Africa/Lagos"

RECEPTIONIST = {
    "id": "u-1", "email": "desk@example.test", "full_name": "Desk Person", "phone": None, "patient_id": None,
    "memberships": [{"hospital_id": HID, "hospital_name": "Test Hospital", "roles": ["receptionist"],
                     "permissions": ["appointments.review", "queue.manage", "patients.register", "patients.search",
                                     "patients.view", "audit.view", "audit.export", "availability.verify"],
                     "doctor_id": None}],
}
DOCTORS = [{"id": "d-1", "display_name": "Dr. Ada", "department_name": "General", "department_id": "dep-1",
            "hospital_id": HID, "title": "Dr.", "specialty": None, "is_accepting_appointments": True},
           {"id": "d-2", "display_name": "Dr. Bayo", "department_name": "General", "department_id": "dep-1",
            "hospital_id": HID, "title": "Dr.", "specialty": None, "is_accepting_appointments": True}]


def appointment(**kw):
    base = {"id": "a-1", "status": "requested", "hospital_id": HID, "hospital_name": "Test Hospital",
            "hospital_timezone": TZ, "department_id": "dep-1", "department_name": "General", "doctor_id": "d-1",
            "doctor_name": "Dr. Ada", "start_at": "2026-11-02T08:00:00+00:00", "end_at": "2026-11-02T08:15:00+00:00",
            "proposed_start_at": None, "proposed_end_at": None, "status_reason": None, "reason_for_visit": "Check-up",
            "source": "patient_request", "version": 1, "created_at": "2026-10-30T10:00:00+00:00",
            "allowed_actions": ["approve", "cancel", "propose_reschedule", "reject"],
            "patient": {"id": "p-1", "full_name": "Synthetic Patient", "hospital_patient_number": "TEST-000001"}}
    base.update(kw)
    return base


def entry(**kw):
    base = {"id": "e-1", "queue_id": "q-1", "service_date": "2026-11-02", "hospital_timezone": TZ, "ticket_number": 1,
            "status": "waiting", "position": 1, "queue_paused": False, "checked_in_at": "2026-11-02T07:50:00+00:00",
            "estimate": {"state": "estimated", "min_minutes": 10, "max_minutes": 20, "provisional": True,
                         "explanation": "1 patient(s) ahead of you.", "basis": {}},
            "doctor": {"id": "d-1", "name": "Dr. Ada", "public_status": "available"},
            "allowed_actions": ["call", "cancel", "confirm_arrival", "set_priority", "transfer"], "version": 1,
            "patient": {"id": "p-1", "full_name": "Synthetic Patient", "hospital_patient_number": "TEST-000001"},
            "kind": "appointment", "appointment_id": "a-1", "appointment_start_at": None, "reason_for_visit": None,
            "priority_level": 0, "priority_reason": None, "check_in_method": "self", "arrival_confirmed_at": None,
            "called_at": None, "call_count": 0, "missed_count": 0, "started_at": None, "completed_at": None}
    base.update(kw)
    return base


class FakeClient:
    """Implements the ApiClient methods the UI uses. Records state-changing calls in `calls`."""

    base_url = "http://fake"

    def __init__(self):
        self.on_session_expired = None
        self.calls: list[tuple] = []
        self.lock = threading.Lock()
        self.user = RECEPTIONIST
        self.login_error: Exception | None = None
        self.appointment_rows = [appointment()]
        self.entries = [entry()]
        self.queue_list = [{"id": "q-1", "service_date": "2026-11-02", "department_id": "dep-1",
                            "doctor": {"id": "d-1", "name": "Dr. Ada", "public_status": "available"},
                            "doctor_status": "available", "is_paused": False, "paused_reason": None,
                            "counts": {"waiting": 1}}]
        self.dashboard_error: Exception | None = None
        self.register_responses: list = []
        self.action_error: Exception | None = None
        self.staff_list = [
            {"id": "s-1", "full_name": "Test Receptionist", "email": "desk@test.org", "roles": ["receptionist"],
             "department_name": "General", "phone": "+1 555-0100", "title": "Receptionist", "created_at": "2026-10-01T00:00:00Z"}
        ]
        self.departments_list = [
            {"id": "dep-1", "name": "General Medicine", "description": "Primary care outpatient"}
        ]
        self.notif_report = {
            "sms_sent": 42,
            "email_sent": 88,
            "push_sent": 110,
            "items": [
                {"id": "n-1", "timestamp": "2026-11-02T09:15:00Z", "type": "queue_turn", "channel": "sms,email,push",
                 "title": "It's your turn!", "body": "Please proceed to Room 3"}
            ]
        }

    def _record(self, *call):
        with self.lock:
            self.calls.append(call)

    # session
    @property
    def is_authenticated(self):
        return True

    def login(self, email, password):
        if self.login_error:
            raise self.login_error
        return self.user

    def logout(self):
        self._record("logout")

    def stream(self, path, timeout):
        raise NetworkError("no stream in tests")

    def ensure_fresh(self):
        raise SessionExpired()

    def close(self):
        pass

    # reads
    def get(self, path, **params):
        if path == f"/hospitals/{HID}":
            return {"id": HID, "name": "Test Hospital", "timezone": TZ, "code": "TEST", "slug": "test", "address": None}
        raise AssertionError(f"unexpected GET {path}")

    def doctors(self, hid):
        return DOCTORS

    def dashboard(self, hid):
        if self.dashboard_error:
            raise self.dashboard_error
        return {"date": "2026-11-02", "timezone": TZ,
                "queue": {"waiting": 3, "called": 1, "in_consultation": 1, "completed": 7, "missed": 0, "cancelled": 0},
                "appointments": {"pending_approval": 2, "awaiting_patient_response": 1, "today": 12},
                "doctors": {"available": 1, "on_break": 1},
                "alerts": [{"event_type": "missed", "ticket_number": 4, "doctor_name": "Dr. Ada", "queue_id": "q-1",
                            "entry_id": "e-4", "reason": "No response", "occurred_at": "2026-11-02T09:00:00+00:00"}]}

    def appointments(self, hid, **filters):
        self._record("list_appointments", filters)
        return {"items": list(self.appointment_rows), "next_cursor": None}

    def appointment_history(self, appointment_id):
        return [{"event": "requested", "from_status": None, "to_status": "requested", "actor_role": "patient",
                 "actor_user_id": None, "reason": None, "details": {}, "occurred_at": "2026-10-30T10:00:00+00:00"}]

    def queues(self, hid, day=None):
        return self.queue_list

    def queue_entries(self, queue_id, include_finished=False):
        return list(self.entries)

    def queue_entry_events(self, entry_id):
        return [{"id": "ev-1", "entry_id": entry_id, "event_type": "checked_in", "from_status": None,
                 "to_status": "waiting", "actor_role": "patient", "actor_user_id": None, "reason": None,
                 "details": {}, "occurred_at": "2026-11-02T07:50:00+00:00"}]

    def search_patients(self, hid, q):
        return [{"id": "p-1", "hospital_patient_number": "TEST-000001", "full_name": "Synthetic Patient",
                 "phone": "+2348000000001", "date_of_birth": None, "sex": None, "has_online_account": True,
                 "registered_at": "2026-10-01T10:00:00+00:00"}]

    def doctor_availability(self, hid):
        return []

    def audit_events(self, hid, **filters):
        return {"items": [], "next_cursor": None}

    def list_staff(self, hid):
        return list(self.staff_list)

    def create_staff(self, hid, **data):
        self._record("create_staff", hid, data)
        new_s = {
            "id": f"s-{len(self.staff_list)+1}",
            "full_name": data.get("full_name"),
            "email": data.get("email"),
            "roles": data.get("roles", ["receptionist"]),
            "department_name": "General",
            "phone": data.get("phone"),
            "title": data.get("title"),
            "created_at": "2026-11-02T10:00:00Z",
        }
        self.staff_list.append(new_s)
        return new_s

    def departments(self, hid):
        return list(self.departments_list)

    def create_department(self, hid, **data):
        self._record("create_department", hid, data)
        new_d = {"id": f"dep-{len(self.departments_list)+1}", "name": data.get("name"), "description": data.get("description")}
        self.departments_list.append(new_d)
        return new_d

    def notification_report(self, hid):
        return self.notif_report

    # writes
    def appointment_action(self, appointment_id, action, **body):
        self._record("appointment_action", appointment_id, action, body)
        if self.action_error:
            raise self.action_error
        return appointment(id=appointment_id, status="approved", allowed_actions=[])

    def entry_action(self, entry_id, action, **body):
        self._record("entry_action", entry_id, action, body)
        return entry(id=entry_id)

    def call_next(self, queue_id):
        self._record("call_next", queue_id)
        return entry(status="called")

    def register_patient(self, hid, **data):
        self._record("register_patient", data)
        response = self.register_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake():
    return FakeClient()


@pytest.fixture
def state(fake):
    s = AppState(fake)
    s.hospital = Hospital(HID, "Test Hospital", ["receptionist"], set(RECEPTIONIST["memberships"][0]["permissions"]), TZ)
    s.doctors = DOCTORS
    return s


@pytest.fixture
def config():
    return Config(api_url="http://fake", request_timeout=5, refresh_interval_s=60)


def duplicate_error():
    return ApiError(409, "possible_duplicate", "A patient with matching details is already registered.",
                    {"candidates": [{"patient_id": "p-9", "hospital_patient_number": "TEST-000009",
                                     "full_name": "Same Name", "date_of_birth": None, "phone_last4": "1234"}]})
