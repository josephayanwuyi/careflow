# CareFlow Production Deployment Guide

This guide details the complete process for deploying CareFlow to a production environment (on-premise hospital server or cloud VPS).

---

## 1. System Architecture

In production, CareFlow is deployed with the **Same-Origin Reverse Proxy** model:

```
[ Patients & Doctors (Web) ]        [ Reception Staff (PySide6 Desktop) ]
             \                                      /
              \--- HTTPS (Port 443) ---------------/
                                |
                        [ Nginx / Caddy ]
                         /             \
       / (static web SPA)               /api (REST + SSE)
       v                                v
  [ web/dist ]                [ Gunicorn / Waitress ]
                                        |
                                [ PostgreSQL 16+ ]
                                (btree_gist enabled)
```

> [!IMPORTANT]
> Because CareFlow uses `samesite="Strict"` and `HttpOnly` cookies for token refreshes and CSRF protection, the Web SPA and the API **must** be served on the exact same origin domain (e.g. `https://careflow.hospital.org`).

---

## 2. Pre-Deployment Code & Configuration Clean-up
Before deploying to a server, verify the following:

### A. Environment Configuration & Secrets
1. **Never commit `.env`**: Confirm `.env` is listed in [`.gitignore`](../.gitignore).
2. **Generate Cryptographic Keys**:
   Do **not** reuse keys from local development. Generate 64-character random secrets:
   ```bash
   flask --app wsgi careflow generate-secret
   ```
   Generate two separate keys: one for `SECRET_KEY` and one for `JWT_SECRET_KEY`.
3. **Production Flags**:
   - `APP_ENV=production` (disables synthetic seed tools and enforces strict secrets).
   - `COOKIE_SECURE=true` (enforces HTTPS transmission of session cookies).
   - `RATELIMIT_ENABLED=true` (protects against credential brute-forcing).
   - `CORS_ORIGINS=https://careflow.yourhospital.org`

### B. Clean Seed Data & Synthetic Accounts
- Do **not** run `flask careflow seed-dev` in production. (CareFlow will throw an error if `APP_ENV=production`).
- The hospital, departments, and staff accounts must be created using the production admin CLI (see Step 4).

### C. Build the Web Frontend
```bash
cd web
npm install
npm run typecheck
npm run test
npm run build
```
This produces the production SPA assets in `web/dist/`.

---

## 3. Server Setup (Ubuntu 22.04 / 24.04 LTS)

### Step 1: Install System Dependencies
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx
```

### Step 2: Configure PostgreSQL
```bash
sudo -u postgres psql
```
```sql
CREATE ROLE careflow WITH LOGIN PASSWORD 'YOUR_STRONG_DB_PASSWORD';
CREATE DATABASE careflow_prod OWNER careflow;
\c careflow_prod
CREATE EXTENSION IF NOT EXISTS btree_gist;
\q
```

### Step 3: Deploy Backend Application
Clone the repository to `/opt/careflow`:
```bash
sudo mkdir -p /opt/careflow
sudo chown -R $USER:$USER /opt/careflow
cd /opt/careflow
# Copy or pull project files here
```

Create virtual environment and install runtime requirements:
```bash
cd /opt/careflow/backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Create the production `.env` file (`/opt/careflow/backend/.env`):
```env
APP_ENV=production
SECRET_KEY=GENERATED_SECRET_KEY_HERE
JWT_SECRET_KEY=GENERATED_JWT_SECRET_KEY_HERE
DATABASE_URL=postgresql+psycopg://careflow:YOUR_STRONG_DB_PASSWORD@localhost:5432/careflow_prod
COOKIE_SECURE=true
CORS_ORIGINS=https://careflow.yourhospital.org
RATELIMIT_ENABLED=true
LOG_LEVEL=INFO

# Notifications (configure SMTP and/or Twilio)
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=YOUR_SENDGRID_KEY
SMTP_USE_TLS=true
MAIL_FROM=notifications@careflow.yourhospital.org

SMS_PROVIDER=log
```

Run database migrations:
```bash
.venv/bin/flask --app wsgi db upgrade
```

### Step 4: Bootstrap Hospital & Administrator
```bash
# 1. Create the hospital
.venv/bin/flask --app wsgi careflow create-hospital --code LUTH --name "Lagos University Teaching Hospital"

# 2. Create the first hospital administrator
.venv/bin/flask --app wsgi careflow create-staff \
    --hospital LUTH \
    --email admin@luth.org \
    --name "Hospital Administrator" \
    --role hospital_admin
```
*(The CLI will print the generated temporary password for the admin account).*

---

## 4. Process Management (Systemd & Gunicorn)

Create a systemd unit for the Gunicorn WSGI server:
`/etc/systemd/system/careflow-api.service`:
```ini
[Unit]
Description=CareFlow API Service
After=network.target postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/careflow/backend
EnvironmentFile=/opt/careflow/backend/.env
ExecStart=/opt/careflow/backend/.venv/bin/gunicorn \
    --workers 4 \
    --threads 16 \
    --timeout 120 \
    --bind 127.0.0.1:8000 \
    wsgi:app

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now careflow-api
sudo systemctl status careflow-api
```

---

## 5. Scheduled Maintenance Jobs (Cron)

CareFlow requires two background maintenance tasks:
1. **Appointment Expiration**: expires pending requests and proposals that passed their threshold (every 5 min).
2. **Nightly Queue Closure**: closes past days' queues and records no-shows (nightly).

Add to `/etc/cron.d/careflow`:
```cron
# CareFlow Scheduled Jobs
PATH=/opt/careflow/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin

# Every 5 minutes: expire unanswered appointment proposals & unreviewed requests
*/5 * * * * www-data cd /opt/careflow/backend && .venv/bin/flask --app wsgi careflow expire-appointments >> /var/log/careflow-cron.log 2>&1

# Nightly at 00:05: close previous day's queue entries
5 0 * * * www-data cd /opt/careflow/backend && .venv/bin/flask --app wsgi careflow close-past-queues >> /var/log/careflow-cron.log 2>&1
```

---

## 6. Nginx & SSL Configuration

Create `/etc/nginx/sites-available/careflow`:
```nginx
server {
    server_name careflow.yourhospital.org;

    # Web SPA static files
    root /opt/careflow/web/dist;
    index index.html;

    # Gzip compression
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    # React Router fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API Proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Real-Time Server-Sent Events (SSE) stream
    location /api/v1/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # Health check probe
    location /health/ {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

Enable site and acquire SSL:
```bash
sudo ln -s /etc/nginx/sites-available/careflow /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d careflow.yourhospital.org
```

---

## 7. Packaging the Desktop App (Receptionists)

For hospital reception and administration PCs running Windows:

1. On a Windows build machine:
   ```powershell
   cd desktop
   .venv\Scripts\pip install pyinstaller
   .venv\Scripts\pyinstaller --noconsole --name "CareFlow Desk" --icon=resources/icon.ico careflow_desktop/__main__.py
   ```
2. Configure the target API URL for the installer or system environment:
   ```powershell
   [System.Environment]::SetEnvironmentVariable("CAREFLOW_API_URL", "https://careflow.yourhospital.org", "Machine")
   ```

---

## 8. Backup and Disaster Recovery

### Automated Daily PostgreSQL Backups
Add a daily cron job to dump the database:
```bash
# Daily DB backup at 02:00 AM
0 2 * * * postgres pg_dump -Fc careflow_prod > /var/backups/careflow/careflow_$(date +\%F).dump
```

### Restore Drill:
```bash
pg_restore --clean --if-exists -d careflow_prod /var/backups/careflow/careflow_2026-11-05.dump
```
