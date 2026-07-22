#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

profile=${1:-}
case "$profile" in
  nats|database|granian|commands|status) ;;
  *)
    echo "Usage: $0 {nats|database|granian|commands|status}" >&2
    echo "For rollback, restore a saved .env snapshot with scripts/restore-env.sh or deploy the retained rollback release." >&2
    exit 2
    ;;
esac

fail() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v docker >/dev/null 2>&1 || fail "Docker is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
[ -f .env ] || fail "Create .env from .env.production.example"

read_env() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path
key = sys.argv[1]
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    if line.startswith(key + '='):
        print(line.split('=', 1)[1])
        break
PY
}

show_status() {
  for key in REALTIME_DURABLE_BACKEND REALTIME_EPHEMERAL_BACKEND REALTIME_PRESENCE_BACKEND REALTIME_CONNECTION_OWNERSHIP_BACKEND DATABASE_RUNTIME_ENDPOINT CHAT_READ_BACKEND CHAT_COMMAND_BACKEND VITE_CHAT_COMMAND_BACKEND DJANGO_SERVER; do
    printf '%-40s %s\n' "$key" "$(read_env "$key")"
  done
}

if [ "$profile" = status ]; then
  show_status
  docker compose ps 2>/dev/null || true
  exit 0
fi

app_password=$(read_env NATS_APP_PASSWORD)
rt_password=$(read_env NATS_REALTIME_PASSWORD)
[ -n "$app_password" ] || fail "NATS_APP_PASSWORD is required"
[ -n "$rt_password" ] || fail "NATS_REALTIME_PASSWORD is required"
[ "$app_password" != change-me-nats-app ] || fail "Replace the example NATS_APP_PASSWORD first"
[ "$rt_password" != change-me-nats-realtime ] || fail "Replace the example NATS_REALTIME_PASSWORD first"

set_profile() {
  python3 scripts/lib/update_env.py .env --label "profile-$profile" "$@" >/tmp/profile-backup-path
  info "Saved rollback snapshot: $(cat /tmp/profile-backup-path)"
  docker compose config --quiet
}

wait_healthy() {
  service=$1
  attempts=0
  while :; do
    cid=$(docker compose ps -q "$service")
    [ -n "$cid" ] || fail "$service container was not created"
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")
    [ "$state" = healthy ] && return 0
    case "$state" in exited|dead)
      docker compose logs --tail=150 "$service" >&2 || true
      fail "$service stopped during activation";; esac
    attempts=$((attempts + 1))
    [ "$attempts" -lt 60 ] || { docker compose logs --tail=150 "$service" >&2 || true; fail "$service did not become healthy"; }
    sleep 3
  done
}

case "$profile" in
  nats)
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_EPHEMERAL_BACKEND=local REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local
    docker compose up -d nats redis realtime web worker beat
    for service in nats redis realtime web worker; do wait_healthy "$service"; done
    ;;
  database)
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_EPHEMERAL_BACKEND=local REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local DATABASE_RUNTIME_ENDPOINT=pgbouncer CHAT_READ_BACKEND=django CHAT_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django
    docker compose up -d postgres pgbouncer nats redis web worker beat realtime
    for service in postgres pgbouncer nats redis web worker realtime; do wait_healthy "$service"; done
    docker compose exec -T web python manage.py check
    ;;
  granian)
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_EPHEMERAL_BACKEND=local REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local DATABASE_RUNTIME_ENDPOINT=pgbouncer CHAT_READ_BACKEND=django CHAT_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django DJANGO_SERVER=granian
    docker compose up -d --build web worker beat realtime
    for service in web worker realtime; do wait_healthy "$service"; done
    ;;
  commands)
    central=$(read_env CENTRAL_PAYMENTS_ENABLED)
    case "$central" in True|true|1|yes|on) fail "Axum command cutover is blocked while CENTRAL_PAYMENTS_ENABLED is active";; esac
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_EPHEMERAL_BACKEND=local REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local DATABASE_RUNTIME_ENDPOINT=pgbouncer CHAT_READ_BACKEND=sqlx CHAT_COMMAND_BACKEND=axum VITE_CHAT_COMMAND_BACKEND=axum DJANGO_SERVER=granian
    docker compose up -d --build frontend realtime web worker beat
    for service in frontend realtime web worker; do wait_healthy "$service"; done
    ;;
esac

show_status
