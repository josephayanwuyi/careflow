# API examples

Base URL: `http://localhost:8000/api/v1`. The full schema is at `/api/docs`.

## Errors

Every error uses the same shape:

```json
{"error": {"code": "possible_duplicate", "message": "...", "details": {...}, "request_id": "8c31adfe..."}}
```

| Status | Meaning |
|---|---|
| 401 | Missing, invalid or expired token, or the session has ended (`token_missing`, `token_invalid`, `token_expired`, `session_ended`, `invalid_credentials`) |
| 403 | Authenticated but not permitted in this hospital |
| 404 | Not found, or outside the caller's scope |
| 409 | Conflict (`email_taken`, `possible_duplicate`, `department_exists`, ...) |
| 422 | Validation failed; `details` lists the problem fields |
| 429 | Rate limited |

## Authentication

```bash
# Patient registration
curl -X POST $BASE/auth/register -H "Content-Type: application/json" \
  -d '{"email":"ada@example.test","password":"a-long-password","full_name":"Ada Example","phone":"08031234567"}'

# Sign in (any role)
curl -X POST $BASE/auth/login -H "Content-Type: application/json" \
  -d '{"email":"reception1@example.test","password":"...","client_type":"desktop"}'
# -> {"access_token": "...", "refresh_token": "...", "expires_in": 900, "token_type": "Bearer",
#     "user": {"memberships": [{"hospital_id": "...", "roles": ["receptionist"], "permissions": [...]}]}}

# Refresh (each refresh token works once; re-using an old one ends the session)
curl -X POST $BASE/auth/refresh -H "Content-Type: application/json" -d '{"refresh_token":"..."}'

# Current user / logout
curl $BASE/auth/me -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/auth/logout -H "Authorization: Bearer $TOKEN"
```

## Directory (public)

```bash
curl $BASE/hospitals
curl $BASE/hospitals/$HID/departments
curl "$BASE/hospitals/$HID/doctors?department_id=$DEPT"
```

## Patients (staff)

```bash
# Walk-in registration. A possible duplicate returns 409 with candidates;
# resend with confirm_not_duplicate=true and a duplicate_override_reason to proceed.
curl -X POST $BASE/hospitals/$HID/patients -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"full_name":"Walk In","phone":"08030000000","date_of_birth":"1990-01-01"}'

# Search by hospital number, phone, or name
curl "$BASE/hospitals/$HID/patients?q=DEMO-000001" -H "Authorization: Bearer $TOKEN"
```

## Audit

```bash
curl "$BASE/hospitals/$HID/audit-events?action=patient.registered_walk_in&limit=50" -H "Authorization: Bearer $TOKEN"
# Paginate by passing back next_cursor as ?cursor=...
```

## Schedules and slots

```bash
# Admin: weekly session (hospital local time). weekday 0 = Monday.
curl -X POST $BASE/hospitals/$HID/doctors/$DOC/schedule-templates -H "Authorization: Bearer $ADMIN" \
  -H "Content-Type: application/json" \
  -d '{"weekday":0,"start_time":"09:00","end_time":"13:00","slot_minutes":15,"valid_from":"2026-11-01"}'

# Admin: block a day (omit times) or part of a day, or add an extra session
curl -X POST $BASE/hospitals/$HID/doctors/$DOC/schedule-exceptions -H "Authorization: Bearer $ADMIN" \
  -H "Content-Type: application/json" -d '{"on_date":"2026-11-10","kind":"unavailable","reason":"Leave"}'

# Public: slots for a hospital-local date (times returned in UTC)
curl "$BASE/doctors/$DOC/slots?date=2026-11-02"
```

## Appointments

```bash
# Patient: request (awaits approval). Idempotency-Key makes retries safe.
curl -X POST $BASE/appointments -H "Authorization: Bearer $PATIENT" -H "Idempotency-Key: 3f6c2a9e-req-01" \
  -H "Content-Type: application/json" \
  -d '{"doctor_id":"'$DOC'","start_at":"2026-11-02T09:00:00+01:00","reason_for_visit":"Follow-up"}'

curl "$BASE/appointments/mine" -H "Authorization: Bearer $PATIENT"

# Staff: review queue, then decide. expected_version guards against stale screens (409 stale_version).
curl "$BASE/hospitals/$HID/appointments?status=requested" -H "Authorization: Bearer $STAFF"
curl -X POST $BASE/appointments/$APPT/approve -H "Authorization: Bearer $STAFF" -H "Content-Type: application/json" -d '{"expected_version":1}'
curl -X POST $BASE/appointments/$APPT/reject  -H "Authorization: Bearer $STAFF" -H "Content-Type: application/json" -d '{"reason":"Clinic full"}'
curl -X POST $BASE/appointments/$APPT/propose-reschedule -H "Authorization: Bearer $STAFF" -H "Content-Type: application/json" \
  -d '{"start_at":"2026-11-03T10:00:00+01:00","reason":"Doctor in theatre"}'

# Patient: respond to a proposal, or cancel
curl -X POST $BASE/appointments/$APPT/accept-reschedule  -H "Authorization: Bearer $PATIENT"
curl -X POST $BASE/appointments/$APPT/decline-reschedule -H "Authorization: Bearer $PATIENT" -H "Content-Type: application/json" -d '{}'
curl -X POST $BASE/appointments/$APPT/cancel -H "Authorization: Bearer $PATIENT" -H "Content-Type: application/json" -d '{}'

# Anyone who can see it: full status history
curl "$BASE/appointments/$APPT/history" -H "Authorization: Bearer $TOKEN"
```

Each appointment response includes `allowed_actions` (what the caller may do now) and `version`.

| Conflict code (409) | Meaning |
|---|---|
| `not_a_slot`, `slot_too_soon`, `slot_too_far_ahead` | Time is not bookable |
| `slot_unavailable` | Someone else holds the time |
| `patient_time_conflict` | The patient already has an appointment then |
| `duplicate_request`, `idempotency_key_reused` | Repeat submission |
| `too_many_open_requests` | Patient has too many requests awaiting review |
| `invalid_transition` | Action not allowed in the current status |
| `stale_version` | Someone changed it since you loaded it |

## Notifications

```bash
curl "$BASE/notifications?unread_only=true" -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/notifications/$NID/read -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/notifications/read-all -H "Authorization: Bearer $TOKEN"
```

## Live queue

```bash
# Patient (within the check-in window) or staff: check in an approved appointment
curl -X POST $BASE/appointments/$APPT/check-in -H "Authorization: Bearer $TOKEN"

# Staff: add a registered walk-in to a doctor's queue today
curl -X POST $BASE/hospitals/$HID/queue/walk-ins -H "Authorization: Bearer $STAFF" -H "Content-Type: application/json" \
  -d '{"patient_id":"PATIENT_ID","doctor_id":"DOCTOR_ID"}'

# Staff/doctor: today's queues with counts, then one queue's entries (positions + estimates)
curl "$BASE/hospitals/$HID/queues" -H "Authorization: Bearer $STAFF"
curl "$BASE/queues/$QID/entries" -H "Authorization: Bearer $STAFF"

# Doctor: arrive, become available, then work the queue
curl -X POST $BASE/doctors/me/status -H "Authorization: Bearer $DOCTOR" -H "Content-Type: application/json" -d '{"status":"checked_in"}'
curl -X POST $BASE/doctors/me/status -H "Authorization: Bearer $DOCTOR" -H "Content-Type: application/json" -d '{"status":"available"}'
curl -X POST $BASE/queues/$QID/call-next -H "Authorization: Bearer $DOCTOR"
curl -X POST $BASE/queue-entries/$EID/start    -H "Authorization: Bearer $DOCTOR" -H "Content-Type: application/json" -d '{}'
curl -X POST $BASE/queue-entries/$EID/complete -H "Authorization: Bearer $DOCTOR" -H "Content-Type: application/json" -d '{}'
curl -X POST $BASE/queue-entries/$EID/mark-missed -H "Authorization: Bearer $DOCTOR" -H "Content-Type: application/json" -d '{"reason":"No response"}'

# Break with an expected return (feeds wait estimates)
curl -X POST $BASE/doctors/me/status -H "Authorization: Bearer $DOCTOR" -H "Content-Type: application/json" \
  -d '{"status":"on_break","expected_return_at":"2026-11-02T13:30:00+01:00"}'

# Staff: return a missed patient, transfer, change priority (needs queue.priority.set), pause
curl -X POST $BASE/queue-entries/$EID/return   -H "Authorization: Bearer $STAFF" -H "Content-Type: application/json" -d '{}'
curl -X POST $BASE/queue-entries/$EID/transfer -H "Authorization: Bearer $STAFF" -H "Content-Type: application/json" -d '{"doctor_id":"OTHER_DOCTOR_ID","reason":"Specialist needed"}'
curl -X POST $BASE/queue-entries/$EID/priority -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d '{"level":2,"reason":"Assessed by triage nurse"}'
curl -X POST $BASE/queues/$QID/pause -H "Authorization: Bearer $DOCTOR" -H "Content-Type: application/json" -d '{"reason":"Called to ward"}'

# Patient: my place, position and estimated wait
curl $BASE/queue/me -H "Authorization: Bearer $PATIENT"
# -> [{"ticket_number": 4, "status": "waiting", "position": 3,
#      "estimate": {"state": "estimated", "min_minutes": 25, "max_minutes": 45, "provisional": false,
#                   "explanation": "2 patient(s) ahead of you."},
#      "doctor": {"public_status": "consultation_in_progress", ...}}]

# Doctor availability: public label, staff overview, receptionist verification
curl $BASE/doctors/$DOC/availability
curl $BASE/hospitals/$HID/doctors/availability -H "Authorization: Bearer $STAFF"
curl -X POST $BASE/hospitals/$HID/doctors/$DOC/verify-arrival -H "Authorization: Bearer $STAFF" -H "Content-Type: application/json" -d '{}'
```

| Queue conflict (409) | Meaning |
|---|---|
| `checkin_not_open`, `checkin_closed` | Outside the check-in window |
| `already_checked_in`, `already_in_queue` | The appointment or patient already has a live place |
| `doctor_not_available`, `queue_paused`, `queue_not_today` | Can't call now |
| `patient_already_called` | Someone is already with the doctor |
| `queue_empty` | No eligible waiting patients |
| `too_many_missed_calls` | The patient can't be returned automatically |

## Live updates (SSE)

```bash
curl -N $BASE/stream -H "Authorization: Bearer $TOKEN"
# retry: 5000
#
# event: ready
#
# event: queue_entry
# data: {"id": "...", "type": "queue_entry", "action": "called", "queue_id": "...", "entry_id": "...", "status": "called", ...}
```

Event types: `queue_entry`, `queue` (paused/resumed), `appointment`, `doctor_status`, `queue_changed` (patients: something changed in their queue), `resync` (reload your view), `session_ended`. A `: keepalive` comment is sent every 15 s.

## Dashboard and reports (desk staff)

```bash
curl $BASE/hospitals/$HID/dashboard -H "Authorization: Bearer $STAFF"
curl "$BASE/hospitals/$HID/reports/queue-day.csv?date=2026-11-02" -H "Authorization: Bearer $STAFF" -o queue.csv
curl "$BASE/hospitals/$HID/audit-events/export.csv?occurred_from=2026-11-01T00:00:00%2B01:00" -H "Authorization: Bearer $STAFF" -o audit.csv
```
