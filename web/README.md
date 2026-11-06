# CareFlow Web (Doctor & Patient Web Application)

A responsive React + TypeScript application connecting to the CareFlow REST API. It is designed
primarily for mobile devices (smartphones) for doctors and patients, and works in modern desktop
browsers as well.

## Status

- **Phase 6 (Doctor Interface): Complete.** Doctors can sign in, manage their availability and arrival,
  view today's assigned appointments, receive real-time queue updates, call patients, run consultations,
  pause/resume queues, and view status history.
- **Phase 7 (Patient Interface): Planned next.** Patient registration, hospital/doctor discovery,
  appointment requests, and live queue tracking.

## Run

```powershell
cd web
npm install
npm run dev
```

The Vite dev server runs at `http://localhost:5173` and proxies `/api` to the backend at
`http://127.0.0.1:8000` (or `CAREFLOW_API_URL`).

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `CAREFLOW_API_URL` | `http://127.0.0.1:8000` | Target URL for API proxy and E2E tests |
| `CAREFLOW_E2E_PASSWORD` | _(none)_ | Shared password of seed accounts for running E2E tests |

## Doctor Interface Features

| Feature | Details |
|---|---|
| Authentication | Signs in with `client_type: "web"`. Short-lived access token kept in memory; single-use refresh token stored in an HttpOnly cookie with double-submit CSRF protection. |
| Status & Availability | Explicit doctor state machine: "I've arrived" (`checked_in`), "I'm available" (`available`), "Take a break" (`on_break`) / "Step away" (`temporarily_unavailable`) with return time prompt, and "Leave for the day" (`checked_out`). |
| Live Queue | Real-time queue view showing patients waiting, their tickets, priority badges, and arrival confirmation status. Includes queue pause/resume controls. |
| Consultation Actions | Call next patient, recall, start consultation, mark missed call, and complete consultation with confirmation dialogs. |
| Today's Overview | Today's active appointments and completed consultations with timestamps and ticket numbers. |
| Availability History | Timestamped audit log of all self-reported and reception-verified availability transitions. |
| Real-time Updates | Fetch-based SSE client listening to `/api/v1/stream`, automatically updating views on queue or status events with connection status indicator and fallback polling. |

## Tests

```powershell
cd web

# Unit and component tests (Vitest + React Testing Library)
npm test

# Type checking
npm run typecheck

# Production build test
npm run build

# End-to-end browser tests (Playwright against running backend with seed data)
$env:CAREFLOW_E2E_PASSWORD = "careflow-seed-pass"
npm run e2e
```
