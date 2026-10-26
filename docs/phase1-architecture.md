# CareFlow — Phase 1: Requirements and Architecture

Status: **Draft for review.** Nothing in this document has been implemented yet.
Source specification: `CareFlow.txt`.

---

## 1. Problem understanding

CareFlow coordinates three groups around one authoritative backend:

| Actor | Client | Core job |
|---|---|---|
| Patient | Responsive web app (mobile-first) | Request appointments, see approval status, check in, follow queue position and an honest wait-time range |
| Receptionist | PySide6 desktop app (required) | Review requests, register walk-ins, check patients in, manage the live queue, handle exceptions with recorded reasons |
| Doctor | Responsive web app (phone-friendly) | Declare availability, call next patient, start/complete consultations, handle missed turns |

Key distinctions the design must preserve:

1. **Request ≠ appointment.** A patient submission is a request until a receptionist approves it.
2. **Schedule ≠ queue.** An approved appointment is a booking; a queue entry exists only after check-in (or walk-in registration). They are separate tables with separate state machines.
3. **Backend is the only authority.** Clients never decide permissions, state transitions, queue positions, ticket numbers, or hospital scope.
4. **Software does not triage.** Priority is a staff action with a mandatory reason and audit trail; the system never infers clinical urgency.
5. **Estimates are ranges, labelled provisional when data is thin.**
6. **Multi-tenant by design** (every operational row is scoped to a hospital), single hospital in the first deployment.
7. **Not an EMR.** No diagnoses, notes or clinical data beyond an optional short, patient-supplied "reason for visit".

## 2. Architecture and technology choices

```
 Patient web ─┐
 Doctor web  ─┼─ HTTPS/JSON + SSE ─► Flask API (gunicorn/waitress) ─► PostgreSQL
 Desktop app ─┘                         │  services layer (business rules)
                                        │  LISTEN/NOTIFY → SSE fan-out
                                        └─ notification adapters (in-app now; email/SMS later)
```

### Repository layout (monorepo)

```
CareFlow/
  backend/            Flask API
    careflow/
      api/            route blueprints only (thin: parse → call service → serialize)
      schemas/        marshmallow request/response schemas
      services/       business logic: appointments, queue, estimator, availability, auth, audit
      models/         SQLAlchemy models
      realtime/       SSE + Postgres LISTEN/NOTIFY
      notifications/  channel interface + in-app channel (+ stub email/SMS adapters)
      config.py, extensions.py, errors.py, logging.py
    migrations/       Alembic
    scripts/seed_dev.py
    tests/            unit/ + integration/
  desktop/            PySide6 receptionist app
    careflow_desktop/
      api_client/     httpx client, auth/session handling, typed errors
      workers/        QThreadPool runnables + SSE listener thread
      models/         QAbstractTableModel subclasses
      views/          screens (login, dashboard, appointments, queue, registration, doctors, audit)
    tests/            pytest-qt
  web/                React + TypeScript + Vite (patient and doctor areas, one codebase)
  docs/
  docker-compose.yml  (optional: Postgres only)
```

### Backend stack

| Concern | Choice | Reason |
|---|---|---|
| Framework | Flask 3.x | Spec |
| API organisation/docs | **flask-smorest** + marshmallow | Generates OpenAPI 3 from the same schemas used for validation; actively maintained |
| ORM / migrations | SQLAlchemy 2.x, Flask-Migrate/Alembic | Spec |
| DB driver | psycopg 3 | Native Postgres, LISTEN/NOTIFY support |
| DB | PostgreSQL 16+ | Needed for `SELECT … FOR UPDATE`, exclusion constraints (`btree_gist`), partial unique indexes, LISTEN/NOTIFY |
| Password hashing | argon2-cffi (Argon2id) | Current recommended KDF |
| Tokens | flask-jwt-extended: 15-min access JWT + rotating refresh token stored hashed server-side (revocable) | Works for desktop (bearer) and web; logout and forced revocation are real |
| Rate limiting | Flask-Limiter | Login, registration, password reset, appointment submission |
| CORS | flask-cors with explicit origin allow-list from env | Spec |
| Logging | structlog → JSON lines, request ID per request, PII-scrubbing processor | Spec |
| WSGI | gunicorn (Linux production) / waitress (Windows dev) | Spec |
| Tests | pytest, pytest-postgresql or a dedicated test DB, factory functions | Concurrency tests must run on real Postgres, not SQLite |

### Desktop stack

PySide6, Qt model/view (`QAbstractTableModel` + `QSortFilterProxyModel`), `httpx` in `QRunnable` workers (UI thread never blocks), a dedicated SSE listener `QThread` that emits Qt signals, automatic reconnect with backoff and a periodic-refresh fallback. Tokens kept in memory only (re-login per app session; optional OS keyring later). Packaged with PyInstaller for Windows.

### Web stack

One React + TypeScript + Vite app with two role-guarded areas (`/patient/*`, `/doctor/*`), sharing one API client. Plain CSS with CSS variables (no heavy UI framework). Refresh token in an `HttpOnly; Secure; SameSite=Strict` cookie, access token in memory; CSRF double-submit token on the refresh endpoint. SSE via `fetch`-based streaming so the bearer token never appears in a URL.

### Real-time

SSE endpoint `/api/v1/stream`. Services write domain events inside the same DB transaction and issue `pg_notify` on commit; each API worker LISTENs and pushes to the subscribed connections **after re-checking authorisation scope** (patient → own appointments/entries only; doctor → own queue; receptionist → own hospital). Payloads contain IDs, statuses, positions and estimates — never names. Clients reconnect with `Last-Event-ID`; on gap they re-fetch.

## 3. Database model (initial)

All PKs are UUIDv4 (`id`). All tables have `created_at`, `updated_at` (`timestamptz`, UTC). Every operational table carries `hospital_id` for tenant scoping and indexing.

| Entity | Key fields | Constraints / indexes |
|---|---|---|
| **hospitals** | name, slug, timezone (IANA, default `Africa/Lagos`), policy JSONB (check-in window, grace minutes, missed-turn rule, fallback consult minutes, self check-in allowed) | slug unique |
| **departments** | hospital_id, name, is_active | unique (hospital_id, name) |
| **users** | email (citext), phone, password_hash, full_name, is_active, last_login_at | email unique; phone unique where not null |
| **staff_memberships** | user_id, hospital_id, role (`receptionist`, `doctor`, `hospital_admin`), is_active | unique (user_id, hospital_id, role) |
| **role_permissions** | hospital_id, role, permission, allowed | unique (hospital_id, role, permission) — configurable overrides (e.g. `queue.priority.set`, `queue.reorder`) |
| **doctor_profiles** | user_id, hospital_id, department_id, title, specialty (display), default_consult_minutes, is_accepting_appointments | unique (user_id, hospital_id); FK department must be in same hospital (enforced in service + composite FK `(department_id, hospital_id)`) |
| **patient_profiles** | user_id (nullable — walk-ins may have no account), full_name, date_of_birth (optional), sex (optional), phone | index on phone |
| **hospital_patient_records** | hospital_id, patient_id, hospital_patient_number | unique (hospital_id, hospital_patient_number); unique (hospital_id, patient_id) — the per-hospital identifier receptionists search by |
| **doctor_schedule_templates** | doctor_id, hospital_id, weekday, start_time, end_time, slot_minutes, valid_from/to | check start < end |
| **doctor_schedule_exceptions** | doctor_id, date, kind (`unavailable`, `extra_session`), times, reason | |
| **appointments** | hospital_id, department_id, doctor_id, patient_id, start_at, end_at, status, reason_for_visit (short, optional), source (`patient_request`, `staff_booked`), idempotency_key, proposed_start_at/end_at (for reschedule proposals), version | **Exclusion constraint** `EXCLUDE USING gist (doctor_id WITH =, tstzrange(start_at,end_at) WITH &&) WHERE status IN ('approved','checked_in')` prevents double-booking at DB level; unique (patient_id, idempotency_key); index (hospital_id, status, start_at), (doctor_id, start_at) |
| **appointment_status_history** | appointment_id, from_status, to_status, actor_user_id, reason, occurred_at | append-only; index (appointment_id, occurred_at) |
| **queues** | hospital_id, doctor_id (nullable), department_id, service_date (hospital-local date), is_paused, next_ticket_number | unique (doctor_id, service_date); this row is the **lock target** for all mutations on that queue |
| **queue_entries** | queue_id, hospital_id, patient_id, appointment_id (nullable), ticket_number (display only), kind (`appointment`, `walk_in`), status, priority_level (0 default), priority_reason, priority_set_by, sort_key (timestamptz), checked_in_at, arrival_confirmed_at/by, entered_queue_at, called_at, call_count, started_at, completed_at, version | unique (queue_id, ticket_number); unique (appointment_id) where not null; partial unique: one *active* entry per (patient_id, queue.service_date); index (queue_id, status, priority_level desc, sort_key) |
| **queue_events** | queue_id, queue_entry_id, event_type, from_status, to_status, actor_user_id, reason, payload JSONB (no PII), occurred_at | append-only |
| **doctor_status_events** | doctor_id, hospital_id, status, source (`self_reported`, `staff_verified`), note, expected_return_at, occurred_at | append-only; current status = latest row (cached on doctor_profiles.current_status for fast reads) |
| **consultation_sessions** | queue_entry_id (unique), doctor_id, started_at, ended_at, paused_seconds, outcome (`completed`, `abandoned`) | duration source for estimator |
| **notifications** | user_id, hospital_id, type, title, body (no clinical data), related_type/id, read_at | index (user_id, read_at) |
| **notification_deliveries** | notification_id, channel (`in_app`, `email`, `sms`), status, attempts, last_error | for future external channels |
| **audit_events** | hospital_id, actor_user_id, actor_role, action, target_type, target_id, reason, request_id, ip (truncated), occurred_at, metadata JSONB | append-only: DB trigger rejects UPDATE/DELETE; app DB role has INSERT/SELECT only |
| **refresh_tokens** | user_id, token_hash, family_id, expires_at, revoked_at, user_agent | reuse of a rotated token revokes the whole family |
| **idempotency_records** | user_id, key, endpoint, response_hash, status_code, created_at | unique (user_id, key, endpoint); TTL cleanup |

Data minimisation: no national ID numbers, no diagnoses, no clinical notes. DOB and sex optional and only if the hospital needs them for record matching.

## 4. State machines

### 4.1 Appointment

```
              ┌──────────── reject (reason) ───────────► REJECTED
              │
 REQUESTED ───┼── approve ──► APPROVED ──check-in──► CHECKED_IN ──consult done──► COMPLETED
   │          │                │   ▲                      │
   │          │                │   │ patient accepts      └── (cancel by staff, reason) ─► CANCELLED
   │          └─ propose new ──┼───┴── RESCHEDULE_PROPOSED ── patient declines/expires ─► CANCELLED
   │             time          │
   │                           ├── no check-in by start + grace ──► NO_SHOW
   │                           └── cancel (patient or staff) ─────► CANCELLED
   ├── cancel by patient ──► CANCELLED
   └── not reviewed before start time ──► EXPIRED
```

Refinements vs. the suggested list:
- **"Rescheduled" is an event, not a state.** When a patient accepts a proposed time, the appointment returns to `APPROVED` with new times; history records `rescheduled`. This avoids a state that overlaps with `APPROVED`.
- **"Reschedule requested" → `RESCHEDULE_PROPOSED`**, meaning staff proposed a time and the patient must accept or decline. (Patient-initiated reschedule = cancel + new request in v1.)
- **`EXPIRED`** added: a request never reviewed before its slot passed. Distinct from rejection.
- The live consultation progress lives on the **queue entry**, not the appointment; the appointment moves to `COMPLETED` when its queue entry completes.
- Terminal: `REJECTED`, `CANCELLED`, `EXPIRED`, `COMPLETED`, `NO_SHOW`. Corrections to terminal states require `hospital_admin` + reason (correction workflow, audited).

### 4.2 Queue entry

```
             check-in / walk-in registration
                       │
                       ▼
   ┌────────────► WAITING ──call──► CALLED ──start──► IN_CONSULTATION ──complete──► COMPLETED
   │                │  ▲               │  │
   │ return         │  │ recall        │  └─ no response (doctor/receptionist marks) ──► MISSED
   │ (receptionist) │  └───────────────┘                                                 │
   └────────────────┼────────────────────────────────────────────────────────────────────┘
                    ├── cancel (reason) ─────────────► CANCELLED
                    ├── transfer to other doctor ────► TRANSFERRED (new WAITING entry created in target queue)
                    └── left without being seen ─────► LEFT
```

Rules (defaults, all configurable per hospital where marked ⚙):
- **Ordering:** `priority_level DESC, sort_key ASC, entered_queue_at ASC`. Only entries in `WAITING` are eligible to be called.
- **sort_key** for appointment patients = scheduled `start_at` if checked in no later than `start_at + grace` ⚙ (default 15 min); otherwise the check-in time. Walk-ins = check-in time. This rewards punctuality without letting early arrivals jump the schedule.
- **Priority** can be set only by roles holding `queue.priority.set` ⚙, requires a reason, is audited, and is never computed by the software.
- **Missed call:** `CALLED` → `MISSED` after staff marks no response. The patient may **return** (receptionist action): re-enters `WAITING` with `sort_key = now` ⚙ (default: back of the queue). After `max_missed_calls` ⚙ (default 2) the entry stays `MISSED` and the appointment becomes `NO_SHOW` at end of session.
- **Cancel** removes from eligibility; positions behind shift up automatically (positions are computed, never stored).
- **Change of doctor:** old entry → `TRANSFERRED`, new entry created in the target queue keeping the original `sort_key` (patient does not lose their place) — receptionist only, with reason.
- **Doctor pause/break:** sets queue `is_paused`; entries keep their status; "call next" is refused while paused; estimates include expected break end.
- **Concurrency:** every queue mutation runs in a transaction that first does `SELECT … FROM queues WHERE id = :id FOR UPDATE`. Ticket numbers come from `queues.next_ticket_number` under that lock. Clients send the entry `version`; a stale version returns `409 Conflict`. "Call next" is idempotent per request key, so double-taps cannot call two patients.
- **Visible number:** `ticket_number` (e.g. `A-014`) is display-only; the stable identifier is the entry UUID.

### 4.3 Doctor availability

`OFF_DUTY` (shown to patients as *Expected* when a session is scheduled today) → `CHECKED_IN` → `AVAILABLE` ⇄ `ON_BREAK` / `TEMPORARILY_UNAVAILABLE` → `CHECKED_OUT`.
`CONSULTATION_IN_PROGRESS` is **derived** from an active consultation session, not self-declared. Each status event records `source` (`self_reported` vs `staff_verified`); the desktop shows both. GPS is **out of scope for v1** (explicit check-in only); a later optional, permission-based, single-point geofence check can add a `location_verified` flag without storing coordinates history.

## 5. Waiting-time estimator (v1, explainable)

For an entry with *n* eligible patients ahead:

```
D        = per-doctor consultation durations from the last 60 days (completed sessions, paused time excluded, outliers > 3×IQR dropped)
if |D| >= 10:  m = median(D), lo = P25(D), hi = P75(D), provisional = false
else:          m = doctor.default_consult_minutes or hospital fallback (15), lo = 0.75m, hi = 1.5m, provisional = true

current  = remaining time of in-progress consultation = max(m - elapsed, 2 min)   (0 if none)
offset   = time until doctor available (scheduled start not yet reached, break with expected_return_at)
estimate = offset + current + n × [lo, hi]   → rounded to 5-minute bounds
```

If the doctor is `TEMPORARILY_UNAVAILABLE` with no expected return, or `CHECKED_OUT`, the API returns `status: "unavailable"` with no number. Recomputed on every queue/availability event and pushed via SSE. Prediction error (predicted vs. actual call time) is logged per entry so accuracy can be measured before any claims are made.

## 6. API plan (`/api/v1`, JSON, OpenAPI at `/api/docs`)

Conventions: errors `{"error": {"code", "message", "details", "request_id"}}`; cursor pagination (`?limit=&cursor=`); `Idempotency-Key` header on creating/transition POSTs; `If-Match`/`version` on mutations of queue entries and appointments; 401 unauthenticated, 403 forbidden, 404 for out-of-scope resources (no existence leak), 409 conflict/invalid transition, 422 validation.

| Group | Endpoints (abridged) | Who |
|---|---|---|
| `/auth` | `POST register` (patient), `POST login`, `POST refresh`, `POST logout`, `GET me` | all |
| `/hospitals` | `GET /`, `GET /{id}` | public |
| `/departments` | `GET /hospitals/{id}/departments` | public |
| `/doctors` | `GET ?hospital&department`, `GET /{id}`, `GET /{id}/slots?date=` | public (no personal data beyond name/title/department/status) |
| `/patients` | `GET me`, `PATCH me`; staff: `POST` (walk-in registration with duplicate check), `GET ?q=` (hospital number/phone search), `GET /{id}` | patient (self), receptionist (own hospital) |
| `/appointments` | `POST` (request), `GET ?status&date&doctor`, `GET /{id}`, `GET /{id}/history`, `POST /{id}/approve`, `/reject`, `/propose-reschedule`, `/accept-reschedule`, `/decline-reschedule`, `/cancel`, `/check-in`, `/mark-no-show` | scoped per role |
| `/queue` | `GET /queues?date&doctor&department`, `GET /queues/{id}/entries`, `POST /queues/{id}/walk-ins`, `POST /queues/{id}/call-next`, `POST /entries/{id}/start`, `/complete`, `/mark-missed`, `/return`, `/cancel`, `/transfer`, `/priority`, `/confirm-arrival`, `GET /entries/{id}/estimate`, `GET /me/active` (patient), `POST /queues/{id}/pause`, `/resume` | scoped per role |
| `/availability` | `POST /doctors/me/status`, `GET /doctors/{id}/status-history`, `POST /doctors/{id}/verify` (receptionist) | doctor / receptionist |
| `/notifications` | `GET`, `POST /{id}/read` | self |
| `/audit` | `GET ?target_type&target_id&actor&from&to`, `GET /export.csv` | receptionist (limited), hospital_admin |
| `/stream` | `GET` (SSE) | authenticated, scope-filtered |
| `/health` | `GET /health/live`, `/health/ready` | ops |

Clients can never submit `status`, `position`, `ticket_number`, `hospital_id` (derived from the actor's membership or the doctor), or actor identity.

## 7. Security and privacy

- Argon2id hashing; generic login error messages; account lockout/backoff + rate limits on auth endpoints.
- Short-lived access tokens, rotating refresh tokens with reuse detection, server-side revocation on logout and deactivation.
- Authorisation in a single service-layer policy module: every service call receives the actor and checks role + hospital membership + object ownership (IDOR protection). Out-of-scope objects return 404.
- Tenant isolation tested explicitly (cross-hospital integration tests).
- Marshmallow validation on all input; unknown fields rejected.
- ORM-only DB access; no raw string SQL.
- Strict CORS allow-list; security headers (HSTS, CSP for the web app, `X-Content-Type-Options`, `Referrer-Policy`).
- Safe errors (no stack traces in responses); request IDs for correlation.
- Log scrubbing: no passwords, tokens, names or phone numbers in logs; IDs only.
- SSE and notification payloads carry no patient names or clinical data.
- Append-only audit log enforced in the DB (trigger + restricted grants).
- Secrets only via environment variables (`.env` for dev, never committed; `.env.example` provided).
- NDPA 2023: design supports lawful basis recording, data minimisation, subject access/export, retention policy and breach logging, but **compliance is not claimed**; a checklist of controls requiring legal/operational review (DPIA, DPO designation, data processing agreements, hosting location, retention periods, consent text) will be kept in `docs/privacy-and-compliance.md`.
- Synthetic data only in development and tests; seed script refuses to run when `APP_ENV=production`.
- Backups: documented `pg_dump` schedule + restore drill in Phase 9.

## 8. Milestones

| Phase | Deliverable | Verified by |
|---|---|---|
| 1 | This document, agreed | Your review |
| 2 | Backend skeleton, config, DB + migrations, auth (register/login/refresh/logout), RBAC policy module, hospitals/departments/doctors/patients, OpenAPI docs, health checks, seed script | pytest unit + integration suite passing on Postgres |
| 3 | Schedules/slots, appointment requests, approve/reject/reschedule/cancel, history + audit, idempotency, double-booking constraint | Lifecycle + conflict + concurrency tests |
| 4 | Queues, check-in, walk-ins, call/start/complete/miss/return/transfer/priority, doctor availability, estimator, SSE | Queue ordering, concurrency (parallel threads against Postgres), estimator tests |
| 5 | PySide6 desktop app: all seven screens against the real API, live updates, error/expiry handling | pytest-qt tests + manual run |
| 6 | Doctor web interface | Component tests + manual run on mobile viewport |
| 7 | Patient web interface | Component tests, accessibility checks (axe), manual mobile run |
| 8 | End-to-end Definition-of-Done scenario across all three clients; authz boundary sweep | Scripted E2E run, results reported honestly |
| 9 | Production config, gunicorn, logging, backups, PyInstaller package, deployment checklist, outstanding-risk list | Checklist review |

## 9. Acceptance criteria — first working release

The 16-step Definition of Done in the specification, made testable:

1. Patient registers, logs in, selects hospital → department → doctor → open slot, submits; UI states "Request submitted — awaiting approval". Duplicate submit with same idempotency key creates one request.
2. Request appears in desktop *Appointments* within 5 s (SSE) or on next refresh.
3. Receptionist approves; history row + audit event written; slot cannot be approved for another patient (DB constraint verified by concurrent test).
4. Patient sees `Approved` with date/time/doctor and check-in instructions; in-app notification created.
5. Patient checks in only inside the hospital's check-in window; outside it gets a clear refusal.
6. Backend creates exactly one queue entry with ticket number and computed position.
7. Receptionist sees the entry in *Live Queue* with check-in time and estimate.
8. Doctor sees their queue (and only theirs).
9. Doctor sets `AVAILABLE`; patients and desktop see the change.
10. Doctor calls next; the correct entry per documented ordering moves to `CALLED`; double "call next" does not call two patients.
11. Doctor starts and completes the consultation; consultation session recorded with duration.
12. Positions behind shift; appointment becomes `COMPLETED`.
13. Patient's position and estimate update live.
14. Receptionist/admin can view appointment history, queue events and audit log for that appointment.
15. Patient A cannot read patient B's data; doctor cannot read another doctor's queue; staff of hospital X cannot read hospital Y (all return 404/403 in tests).
16. Concurrent mutations on one queue never produce duplicate ticket numbers, two `CALLED` entries for one doctor, or lost updates; invalid transitions return 409; desktop shows a clear message on API unavailability or session expiry and recovers on reconnect.

Plus: reproducible local setup, migrations, automated tests, OpenAPI docs, run instructions for each app.
