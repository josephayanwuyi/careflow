# Design decisions and known limitations

Defaults adopted for the open Phase 1 questions (all can be revisited):

| Question | Default used |
|---|---|
| Local database | Portable PostgreSQL 18.6 in `%LOCALAPPDATA%\CareFlow` (no Docker or admin rights available) |
| How staff identify patients | Hospital-issued patient number (`CODE-000123`) plus phone; no national ID number stored |
| Check-in policy | To implement in Phase 4: patient self check-in within a window; receptionist arrival confirmation recorded separately |
| Doctor schedules | Managed by `hospital_admin` (API in Phase 3); first admin is created by CLI |

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

## Known limitations / to revisit

- **Account lockout can be abused to lock out a real user** by deliberately failing logins. Rate limiting reduces this. A production deployment should add monitoring, and possibly CAPTCHA or progressive delays instead of a hard lock.
- **Rate-limit storage is in-memory by default,** so it is per process. With several gunicorn workers, set `RATELIMIT_STORAGE_URI` to Redis.
- **The web refresh-token cookie flow (HttpOnly cookie + CSRF)** is deferred to Phase 7. For now, refresh tokens travel in the JSON body, which suits the desktop app.
- **Duplicate detection only checks within one hospital.** A walk-in who already has an online account isn't linked automatically yet. Phase 3 links accounts when a patient first books at a hospital.
- **No admin endpoint yet** for deactivating staff or editing role-permission overrides. Use the database or extend the CLI. Planned with the desktop admin screens.
- **`gunicorn` is pinned for Linux deployments but was not installed or tested** on this Windows development machine.
