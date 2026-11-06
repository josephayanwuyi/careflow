# Design decisions and known limitations

Defaults adopted for the open Phase 1 questions (all can be revisited):

| Question | Default used |
|---|---|
| Local database | Portable PostgreSQL 18.6 in `%LOCALAPPDATA%\CareFlow` (no Docker or admin rights available) |
| How staff identify patients | Hospital-issued patient number (`CODE-000123`) plus phone; no national ID number stored |
| Check-in policy | Patients may self check-in from 60 min before to 60 min after the start (configurable, can be disabled). Staff arrival confirmation is recorded separately and can be made mandatory before calling (`require_arrival_confirmation`) |
| Doctor schedules | Managed by `hospital_admin` via the API (`schedules.manage`); first admin is created by CLI |

## Phase 2 decisions

- **Sessions over stateless JWTs.** Access tokens (15 min) carry a session ID (`sid`), and every request checks that the session hasn't been revoked or expired. Logout, password change, deactivation and refresh-token reuse therefore take effect immediately. This costs one indexed lookup per request.
- **Refresh tokens** are random, single-use, and stored only as SHA-256 hashes. Re-using one revokes the whole session (theft detection). Sessions have an absolute lifetime (`SESSION_MAX_HOURS`, default 12).
- **Roles are loaded from the database on every request,** never from token claims, so revoking a role takes effect immediately.
- **Hospital scope is in the URL** (`/hospitals/{id}/...`) and checked against the caller's membership. Clients cannot choose a hospital they don't belong to.
- **Out-of-scope objects return 404,** for example a patient with no record at the caller's hospital. A missing role at a hospital returns 403.
- **Patients are global, hospital records are per hospital.** Staff only see patients with a `hospital_patient_records` row at their hospital.
- **Patient numbers** come from a per-hospital counter allocated under a row lock. Walk-in registration also takes that lock, so two receptionists can't both register the same person at once (covered by a concurrency test).
- **The audit log is append-only in the database.** A trigger rejects UPDATE and DELETE. Audit rows store IDs, never names or phone numbers, and IP addresses are truncated to /24 (IPv4) or /48 (IPv6).
- **Logs** are JSON lines with a request ID. A scrubber redacts password, token, name, phone and email keys.
- **Passwords** use Argon2id, with a 10-character minimum. Unknown emails still run a hash check so failed logins take similar time. Accounts lock for 15 minutes after 5 failures.

## Phase 3 decisions

- **Slots are computed, not stored.** Weekly templates plus per-date exceptions (unavailable / extra session) are expanded on demand in the hospital's timezone. A request or booking must match a generated slot exactly.
- **What holds a doctor's time.** `approved`, `checked_in`, and a pending staff reschedule proposal hold time; plain requests do not. Several patients can request the same slot, and the first approval wins.
- **Double-booking is prevented by the database.** A stored generated column `held_range` and two PostgreSQL exclusion constraints (per doctor and per patient) reject overlaps even under concurrent approvals or if application checks are bypassed. Tests cover both cases.
- **"Rescheduled" is an event, not a status.** Staff propose a time (`reschedule_proposed`, which holds the new time and releases the old). Accepting returns the appointment to `approved` at the new time. Declining cancels it. A patient who wants a different time cancels and requests again.
- **`expired`** is used for requests nobody reviewed, and proposals nobody answered, before their time passed. Expiry runs on staff and patient list endpoints and via `flask careflow expire-appointments`, which should be scheduled every few minutes in production.
- **Optimistic concurrency.** Every appointment has a `version`; actions accept `expected_version`, and a mismatch returns `409 stale_version`. All status changes also lock the row.
- **Idempotency** comes from the `Idempotency-Key` header on `POST /appointments`. The same key and payload return the original (200); the same key with a different payload returns 409. This holds under concurrent double submission (tested).
- **Visibility.** The patient, reviewing staff of that hospital, and the assigned doctor can see an appointment; anyone else gets 404. Patients see the history but not staff user IDs. Doctors can list only their own appointments.
- **Notifications** are in-app rows written in the same transaction as the change. They contain the time and doctor, never the reason for visit.
- **Database sessions run in UTC,** so every API timestamp is UTC (`+00:00`). Clients convert using `hospital_timezone`.
- **Hospital policy settings** (`hospitals.settings`, with defaults in `services/hospital_settings.py`): `booking_max_days_ahead` 60, `booking_min_lead_minutes` 60, `max_open_requests_per_patient` 5, `patient_cancel_min_minutes` 0, `no_show_grace_minutes` 30.

## Phase 4 decisions

- **One queue per doctor per hospital-local day,** created on first check-in. It is a separate table from appointments: a patient joins the queue only by checking in (appointment) or by staff adding them (walk-in).
- **Stable identifiers.** Queue entries are keyed by UUID; the ticket number shown to patients is display-only and unique only within its queue.
- **Ordering** (`services/queue_rules.py`): priority first, then sort key, then entry time, then ticket. Punctual appointment patients (checked in by start + 15 min) keep their booked time as sort key. Late patients and walk-ins use their check-in time. A returning patient goes to the back.
- **Priority** levels 0–3 require `queue.priority.set` (hospital admins by default, configurable per hospital) and a reason, and are audited. The software never assigns priority; clinical triage stays with qualified staff.
- **Missed turns.** Called → `missed` (doctor or staff). Staff can return the patient to the back of the queue until `max_missed_calls` (2) is reached.
- **Changing doctor** is a transfer: the old entry becomes `transferred` and a new entry in the target doctor's queue keeps the original sort key and priority. Both queues are locked in a fixed order to avoid deadlocks.
- **Cancellation** after check-in cancels the appointment too. Patients can cancel their own place; staff must give a reason.
- **End of day.** `flask careflow close-past-queues` (run nightly) cancels unseen entries from earlier days, marking their appointments as no-shows. Consultations left open are closed without a duration so they don't skew estimates.
- **Concurrency.** Every queue mutation locks the queue row first. Database constraints back this up: one patient with the doctor per queue (so duplicate "call next" is impossible), one live entry per appointment, one live place per patient per day, unique tickets, and creation of a day's queue that tolerates races. Concurrency tests cover simultaneous check-ins, double check-in, 8 simultaneous "call next" requests, and conflicting cancels.
- **Doctor availability** is explicit and event-based, with no GPS. Transitions are validated: a doctor must check in before becoming available, and can't check out mid-consultation. Status resets to `off_duty` each hospital-local day. Self-reported and staff-verified events are stored separately, and patients see only a derived label.
- **"Call next" requires** the queue to be today's, not paused, and the doctor to be `available`.
- **Live updates (SSE).** Services publish with `pg_notify` inside their transaction, so only committed changes are announced, across all processes. One listener thread per process fans events out to subscribers, filtered by a scope built from the caller's permissions:
  - staff of the hospital get all events;
  - doctors get their own queue's events;
  - patients get their own events, plus a bare `queue_changed` for their queue.

  Payloads carry IDs and statuses only. Clients re-fetch over REST. Events aren't replayed, so on `resync` or reconnect clients reload their view.
- **Wait-time estimation:** see [wait-time-estimation.md](wait-time-estimation.md).

## Phase 5 decisions (desktop app)

- **Separate project:** `desktop/` has its own virtualenv and dependencies (PySide6, httpx). It only talks to the API.
- **Backend additions for the desk:**
  - `GET /hospitals/{id}/dashboard`: counts, pending approvals, doctor statuses, today's queue alerts.
  - `GET /hospitals/{id}/reports/queue-day.csv`: identifies patients by hospital number only.
  - `GET /hospitals/{id}/audit-events/export.csv`.

  Exports require `audit.export`, are capped at 10,000 rows, and are themselves audited.
- **Who can use the desktop app:** sign-in is limited to accounts with a receptionist or hospital-admin membership. This is a usability gate only; the API enforces permissions regardless.
- **Buttons reflect the server's rules:** enabled actions come from the API's `allowed_actions` combined with the user's permissions, so the UI never offers an action the server would refuse because of state.
- **Live updates** are hints that trigger debounced reloads of the visible screen. Hidden screens reload when next opened. A periodic refresh (60 s, or 15 s while the stream is down) covers missed events.
- **Exported files are the user's responsibility** once saved. The app warns that they may contain personal data.

## Phase 6 decisions (doctor web app)

- **Browser session flow:** web clients authenticate with `client_type: "web"`. The access token (15 min) is held in JavaScript memory only. The refresh token is set as an `HttpOnly` cookie with `SameSite=Strict` and `Path=/api/v1/auth`. Refreshing requires a double-submit readable `careflow_csrf` cookie sent in the `X-CSRF-Token` header.
- **Single-flight token refresh:** in-flight refresh requests and session restoration are deduplicated into a single active promise, avoiding race conditions or session invalidation from StrictMode double-mounting.
- **Fetch-based SSE stream:** browsers cannot supply an `Authorization: Bearer` header with standard `EventSource`. The client uses a `fetch()` streaming reader with reconnect backoff and fallback polling (60 s when live, 15 s when offline).
- **Role routing:** doctor logins redirect to `/doctor`. Receptionist/admin accounts receive a clear banner directing them to the CareFlow desktop app. Patients are routed to `/patient`.
- **Doctor availability:** status updates strictly validate against the server state machine (`checked_in`, `available`, `on_break`, `temporarily_unavailable`, `checked_out`), with confirmation prompts and duration selectors for breaks.

## Known limitations / to revisit

- **Account lockout can be abused to lock out a real user** by deliberately failing logins. Rate limiting reduces this. A production deployment should add monitoring, and possibly CAPTCHA or progressive delays instead of a hard lock.
- **Rate-limit storage is in-memory by default,** so it is per process. With several gunicorn workers, set `RATELIMIT_STORAGE_URI` to Redis.
- **The web refresh-token cookie flow (HttpOnly cookie + CSRF)** is implemented for web clients in Phase 6. Desktop clients continue to receive tokens in the response body.
- **Duplicate detection only checks within one hospital.** A walk-in who already has an online account isn't linked automatically yet. Phase 3 links accounts when a patient first books at a hospital.
- **No admin endpoint yet** for deactivating staff or editing role-permission overrides. Use the database or extend the CLI. Planned with the desktop admin screens.
- **`gunicorn` is pinned for Linux deployments but was not installed or tested** on this Windows development machine.
- **Blocking a doctor's time does not affect existing approved appointments.** Staff must reschedule them; the desktop app should flag these (Phase 5).
- **Reschedule proposals are limited to the same doctor.** Changing doctor means cancel and rebook.
- **There is no endpoint yet for editing hospital policy settings;** they are stored as JSON on the hospital row.
- **No-show is a manual staff action.** Automatic no-show after check-in rules are known comes in Phase 4.
- **Email and SMS notification channels are not implemented;** only in-app notifications exist.
- **Queues are per doctor.** Department-level "first available doctor" queues are not implemented; staff transfer patients between doctors instead.
- **Each SSE client holds a server thread.** Run waitress with enough threads (e.g. `--threads=64`), or gunicorn with `gthread`/`gevent` workers. Plain sync workers are not suitable for `/stream`.
- **Browsers can't send an `Authorization` header with `EventSource`.** The web app will use a fetch-based SSE client (Phase 6/7). Tokens are never accepted in URLs.
- **Estimate accuracy has not been measured.** The data is captured, but no pilot data exists yet.
