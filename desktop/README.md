# CareFlow Desk (receptionist desktop app)

A native PySide6 (Qt) application for hospital reception and administration staff. It talks only
to the CareFlow API; it never connects to the database and never decides permissions itself.

## Run

```powershell
cd desktop
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
$env:CAREFLOW_API_URL = "http://127.0.0.1:8000"     # default
.venv\Scripts\python -m careflow_desktop
```

| Variable | Default | Purpose |
|---|---|---|
| `CAREFLOW_API_URL` | `http://127.0.0.1:8000` | API server (use `https://` in deployment) |
| `CAREFLOW_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `CAREFLOW_REFRESH_SECONDS` | `60` | Fallback refresh interval while live updates are connected (15 s while offline) |

Sign in with a receptionist or hospital-admin account. With seed data: `reception1@example.test`
and the seed password. Doctor-only and patient accounts are refused with an explanation.

Logs go to `%LOCALAPPDATA%\CareFlow\desktop-logs\careflow-desktop.log`. They contain no tokens and
no passwords.

## Screens

| Screen (shortcut) | What staff can do |
|---|---|
| Dashboard (Ctrl+1) | Today's waiting / with doctor / completed counts, pending approvals, doctors' statuses, queue alerts |
| Appointments (Ctrl+2) | Filter by status, doctor and date; approve, reject, propose a new time, check in, cancel, mark no-show; full history |
| Live Queue (Ctrl+3) | Per-doctor queue with position, status, arrival and wait estimate; call next (Ctrl+N), confirm arrival, return a missed patient, transfer, change priority (if permitted), remove, pause/resume, consultation actions |
| Patients (Ctrl+4) | Search by hospital number, phone or name; register with duplicate detection; book an appointment; add to today's queue |
| Doctors (Ctrl+5) | Each doctor's status, whether it was self-reported or staff-verified, last update, expected return; verify arrival |
| Audit & Reports (Ctrl+6) | Filtered audit history; export the day's queue report or the audit log as CSV (requires `audit.export`) |

**Keyboard use.**
- F5 reloads the current screen and Ctrl+F jumps to search.
- Buttons have Alt mnemonics, e.g. Alt+A to approve.
- Tables support arrow keys and type-to-select.

**Confirmations.** Consequential actions ask for confirmation. Reject, cancel, transfer and priority changes also require a reason.

## Design

- **`api/client.py`.** Every call has a timeout, and failures become clear messages. On an expired token the client refreshes once and retries; if the session has ended, the app returns to sign-in. Tokens are kept in memory only.
- **`tasks.py`.** All API calls run on a thread pool, and results come back on the UI thread, so the interface never freezes. Results for windows that have closed are dropped.
- **`api/events.py`.** A background thread reads the server's live-update stream and reconnects with backoff. Events only trigger reloads; data always comes from the REST API.
  - The status bar shows whether live updates are on, connecting, or offline.
  - While offline, screens refresh every 15 s.
- **Loading, empty and error states** are shown in place of each table. Later refreshes keep the current data visible.
- **Stale data.** When a change fails because someone else modified the record (`stale_version`), the app explains why and reloads.
- **Buttons follow the server.** Enabled buttons come from the server's `allowed_actions` plus the user's permissions. The server still validates every action.

## Tests

```powershell
.venv\Scripts\python -m pytest            # unit + GUI tests, headless, fake API (no server needed)

# Against a running server with seed data (creates and then cancels one appointment):
$env:CAREFLOW_LIVE_API_URL = "http://127.0.0.1:8000"; $env:CAREFLOW_LIVE_PASSWORD = "<seed password>"
.venv\Scripts\python -m pytest tests\test_live_api.py
```

Headless runs set `QT_QPA_PLATFORM=offscreen` automatically.

## Not yet done

- **Packaging:** a Windows installer (PyInstaller) is planned for Phase 9.
- **Printing:** reports are exported as CSV. Printing directly from the app is not implemented.
- **Admin screens:** there are no screens yet for doctor schedules, departments or staff accounts. Admins use the API (see `docs/api-examples.md`).
