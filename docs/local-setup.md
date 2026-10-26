# Local development setup (Windows)

## 1. PostgreSQL

Development uses a **portable PostgreSQL 18** install (no admin rights or Windows service needed):

| Item | Location |
|---|---|
| Binaries | `%LOCALAPPDATA%\CareFlow\pgsql` |
| Data directory | `%LOCALAPPDATA%\CareFlow\data` |
| Server log | `%LOCALAPPDATA%\CareFlow\postgres.log` |
| Generated passwords | `%LOCALAPPDATA%\CareFlow\db-credentials.txt` (outside the repo) |

Databases: `careflow_dev` and `careflow_test`, owned by role `careflow`. The `btree_gist`
extension is enabled in both.

The server is **not** a Windows service; start it after each reboot:

```powershell
powershell -File scripts\dev-db.ps1 start     # or stop / status
```

### Recreating it on another machine

1. Download the "binaries" zip for Windows x64 from EnterpriseDB and extract `pgsql\` to `%LOCALAPPDATA%\CareFlow\`.
2. `pgsql\bin\initdb -D %LOCALAPPDATA%\CareFlow\data -U postgres -W --auth=scram-sha-256 -E UTF8 --locale=C`
3. Start it with `scripts\dev-db.ps1 start`, then as `postgres`:
   ```sql
   CREATE ROLE careflow LOGIN PASSWORD '...';
   CREATE DATABASE careflow_dev OWNER careflow;
   CREATE DATABASE careflow_test OWNER careflow;
   ```

A natively installed PostgreSQL 16+ or a Docker container works equally well. Only `DATABASE_URL` / `TEST_DATABASE_URL` need to change.

## 2. Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env          # then fill in secrets and DB URLs
.venv\Scripts\flask --app wsgi db upgrade
.venv\Scripts\flask --app wsgi careflow seed-dev     # synthetic data only; prints the seed password
.venv\Scripts\waitress-serve --listen=127.0.0.1:8000 wsgi:app
```

- API docs: http://localhost:8000/api/docs (OpenAPI JSON: `/api/openapi.json`)
- Health: `/health/live`, `/health/ready`

### Tests

```powershell
cd backend
.venv\Scripts\python -m pytest -q
```

Tests need the PostgreSQL server running. Each run rebuilds the test schema through the real
Alembic migrations (downgrade to base, then upgrade), so migrations are exercised every time.

### Admin CLI

```powershell
flask --app wsgi careflow create-hospital --code LUTH --name "Example Teaching Hospital"
flask --app wsgi careflow create-staff --hospital LUTH --email admin@hospital.example --name "Jane Admin" --role hospital_admin
flask --app wsgi careflow generate-secret
```

After the first `hospital_admin` exists, more staff and departments are created through the API
(`POST /api/v1/hospitals/{id}/staff`, `POST /api/v1/hospitals/{id}/departments`).

## Seed data

`careflow seed-dev` creates hospital `DEMO` (flagged `is_seed_data`) with 3 departments, an admin,
2 receptionists, 3 doctors and 3 patients. Every account uses an `@example.test` email. Set
`SEED_PASSWORD` to choose their shared password, otherwise one is generated and printed. The
command refuses to run when `APP_ENV=production`.
