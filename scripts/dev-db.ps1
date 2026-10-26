# Start/stop/status for the portable local PostgreSQL used in development.
# Usage:  powershell -File scripts\dev-db.ps1 start|stop|status
param([ValidateSet("start", "stop", "status")][string]$Action = "status")

$root = Join-Path $env:LOCALAPPDATA "CareFlow"
$pgCtl = Join-Path $root "pgsql\bin\pg_ctl.exe"
$data = Join-Path $root "data"
$log = Join-Path $root "postgres.log"

if (-not (Test-Path $pgCtl)) { throw "PostgreSQL not found at $pgCtl. See docs/local-setup.md." }

switch ($Action) {
    "start"  { & $pgCtl -D $data -l $log start -w }
    "stop"   { & $pgCtl -D $data stop -m fast }
    "status" { & $pgCtl -D $data status }
}
