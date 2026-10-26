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
