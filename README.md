# CareFlow

Hospital Appointment and Queue Management System.

| Component | Path | Status |
|---|---|---|
| Flask REST API | `backend/` | Phases 2–4 implemented and tested (auth, patients, schedules, appointments, notifications, live queue, doctor availability, wait estimates, live updates) |
| PySide6 receptionist desktop app | `desktop/` | Phase 5 implemented and tested: all seven screens against the real API, live updates (see [`desktop/README.md`](desktop/README.md)) |
| Patient & doctor web app (React) | `web/` | Phase 6 implemented and tested: doctor smartphone interface with live queue, availability controls, and E2E tests (see [`web/README.md`](web/README.md)); Phase 7 patient interface upcoming |

- Design and requirements: [`docs/phase1-architecture.md`](docs/phase1-architecture.md)
- Local setup: [`docs/local-setup.md`](docs/local-setup.md)
- Production deployment: [`docs/deployment.md`](docs/deployment.md)
- API usage examples: [`docs/api-examples.md`](docs/api-examples.md)
- Design decisions and known limitations: [`docs/decisions.md`](docs/decisions.md)
- Wait-time estimator (model, assumptions, limitations): [`docs/wait-time-estimation.md`](docs/wait-time-estimation.md)

Interactive API docs are served at `http://localhost:8000/api/docs` when the backend is running.
