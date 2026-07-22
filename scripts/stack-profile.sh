#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

profile=${1:-}
case "$profile" in
  nats|database|granian|reads-shadow|reads|interactions-shadow|interactions|mutations-shadow|mutations|calls-shadow|calls|conversations-shadow|conversations|conversations-rollback|support-shadow|support|support-rollback|attachments-shadow|attachments|attachments-rollback|outbox|outbox-rollback|media-shadow|media|media-rollback|commands|hot-paths|final|efficient|status) ;;
  *)
    echo "Usage: $0 {nats|database|granian|reads-shadow|reads|interactions-shadow|interactions|mutations-shadow|mutations|calls-shadow|calls|conversations-shadow|conversations|conversations-rollback|support-shadow|support|support-rollback|attachments-shadow|attachments|attachments-rollback|outbox|outbox-rollback|media-shadow|media|media-rollback|commands|hot-paths|final|efficient|status}" >&2
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
  for key in AXUM_DATA_PLANE_REQUIRED REALTIME_DURABLE_BACKEND REALTIME_OUTBOX_PUBLISHER REALTIME_EPHEMERAL_BACKEND REALTIME_PRESENCE_BACKEND REALTIME_CONNECTION_OWNERSHIP_BACKEND DATABASE_RUNTIME_ENDPOINT CHAT_READ_BACKEND CHAT_COMMAND_BACKEND CHAT_INTERACTION_BACKEND CHAT_MESSAGE_MUTATION_BACKEND CHAT_CALL_RUNTIME_BACKEND CHAT_ATTACHMENT_BACKEND CHAT_CONVERSATION_COMMAND_BACKEND SUPPORT_DATA_BACKEND VITE_CHAT_COMMAND_BACKEND VITE_CHAT_INTERACTION_BACKEND VITE_CHAT_MESSAGE_MUTATION_BACKEND VITE_CHAT_CALL_RUNTIME_BACKEND VITE_CHAT_ATTACHMENT_BACKEND VITE_CHAT_CONVERSATION_COMMAND_BACKEND VITE_SUPPORT_DATA_BACKEND VITE_CHAT_READ_BACKEND MEDIA_PROCESSING_BACKEND MEDIA_WORKER_DJANGO_FALLBACK_ENABLED SQLX_MIN_CONNECTIONS SQLX_MAX_CONNECTIONS SQLX_ACQUIRE_TIMEOUT_MS REALTIME_HTTP_READ_CONCURRENCY REALTIME_HTTP_WRITE_CONCURRENCY REALTIME_HTTP_REQUEST_TIMEOUT_MS GRANIAN_WORKERS DJANGO_SERVER; do
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

prepare_chat_data_plane() {
  # Django remains the schema owner and Celery owns durable push/storage jobs.
  docker compose up -d --build web worker beat
  for service in web worker; do wait_healthy "$service"; done
  docker compose exec -T web python manage.py migrate --noinput
}

case "$profile" in
  nats)
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_EPHEMERAL_BACKEND=local REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local
    docker compose up -d nats redis realtime web worker beat
    for service in nats redis realtime web worker; do wait_healthy "$service"; done
    ;;
  database)
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_EPHEMERAL_BACKEND=local REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local DATABASE_RUNTIME_ENDPOINT=pgbouncer CHAT_READ_BACKEND=django CHAT_COMMAND_BACKEND=django CHAT_INTERACTION_BACKEND=django CHAT_MESSAGE_MUTATION_BACKEND=django CHAT_CALL_RUNTIME_BACKEND=django CHAT_ATTACHMENT_BACKEND=django CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_INTERACTION_BACKEND=django VITE_CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_CALL_RUNTIME_BACKEND=django VITE_CHAT_ATTACHMENT_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_READ_BACKEND=django
    docker compose up -d postgres pgbouncer nats redis web worker beat realtime
    for service in postgres pgbouncer nats redis web worker realtime; do wait_healthy "$service"; done
    docker compose exec -T web python manage.py check
    ;;
  granian)
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_EPHEMERAL_BACKEND=local REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local DATABASE_RUNTIME_ENDPOINT=pgbouncer CHAT_READ_BACKEND=django CHAT_COMMAND_BACKEND=django CHAT_INTERACTION_BACKEND=django CHAT_MESSAGE_MUTATION_BACKEND=django CHAT_CALL_RUNTIME_BACKEND=django CHAT_ATTACHMENT_BACKEND=django CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_INTERACTION_BACKEND=django VITE_CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_CALL_RUNTIME_BACKEND=django VITE_CHAT_ATTACHMENT_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_READ_BACKEND=django DJANGO_SERVER=granian
    docker compose up -d --build web worker beat realtime
    for service in web worker realtime; do wait_healthy "$service"; done
    ;;
  reads-shadow)
    set_profile CHAT_READ_BACKEND=sqlx_shadow CHAT_CONVERSATION_COMMAND_BACKEND=django CHAT_INTERACTION_BACKEND=django CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_READ_BACKEND=django VITE_CHAT_INTERACTION_BACKEND=django VITE_CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build realtime
    wait_healthy realtime
    ;;
  reads)
    set_profile CHAT_READ_BACKEND=sqlx CHAT_CONVERSATION_COMMAND_BACKEND=django CHAT_INTERACTION_BACKEND=django CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_READ_BACKEND=sqlx VITE_CHAT_INTERACTION_BACKEND=django VITE_CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ;;
  interactions-shadow)
    set_profile CHAT_COMMAND_BACKEND=django CHAT_INTERACTION_BACKEND=sqlx_shadow CHAT_MESSAGE_MUTATION_BACKEND=django CHAT_CALL_RUNTIME_BACKEND=django CHAT_ATTACHMENT_BACKEND=django CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_INTERACTION_BACKEND=django VITE_CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build realtime
    wait_healthy realtime
    ;;
  interactions)
    set_profile CHAT_COMMAND_BACKEND=django CHAT_INTERACTION_BACKEND=axum CHAT_MESSAGE_MUTATION_BACKEND=django CHAT_CALL_RUNTIME_BACKEND=django CHAT_ATTACHMENT_BACKEND=django CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_INTERACTION_BACKEND=axum VITE_CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ;;
  mutations-shadow)
    set_profile CHAT_COMMAND_BACKEND=django CHAT_MESSAGE_MUTATION_BACKEND=sqlx_shadow CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_MESSAGE_MUTATION_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build realtime
    wait_healthy realtime
    ;;
  mutations)
    set_profile CHAT_COMMAND_BACKEND=django CHAT_MESSAGE_MUTATION_BACKEND=axum CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_MESSAGE_MUTATION_BACKEND=axum VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ;;
  calls-shadow)
    set_profile REALTIME_EPHEMERAL_BACKEND=nats CHAT_COMMAND_BACKEND=django CHAT_CALL_RUNTIME_BACKEND=sqlx_shadow CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_CALL_RUNTIME_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build realtime
    wait_healthy realtime
    ;;
  calls)
    set_profile REALTIME_EPHEMERAL_BACKEND=nats CHAT_COMMAND_BACKEND=django CHAT_CALL_RUNTIME_BACKEND=axum CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_CALL_RUNTIME_BACKEND=axum VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ;;
  conversations-shadow)
    set_profile CHAT_CONVERSATION_COMMAND_BACKEND=sqlx_shadow VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    prepare_chat_data_plane
    docker compose up -d --build realtime
    wait_healthy realtime
    ;;
  conversations)
    set_profile CHAT_CONVERSATION_COMMAND_BACKEND=axum VITE_CHAT_CONVERSATION_COMMAND_BACKEND=axum
    prepare_chat_data_plane
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ./scripts/verify-axum-runtime.sh
    ;;
  conversations-rollback)
    set_profile CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ;;
  support-shadow)
    set_profile REALTIME_EPHEMERAL_BACKEND=nats SUPPORT_DATA_BACKEND=sqlx_shadow VITE_SUPPORT_DATA_BACKEND=django
    docker compose up -d --build web worker beat
    for service in web worker; do wait_healthy "$service"; done
    docker compose exec -T web python manage.py migrate --noinput
    docker compose up -d --build realtime
    wait_healthy realtime
    ;;
  support)
    set_profile REALTIME_EPHEMERAL_BACKEND=nats SUPPORT_DATA_BACKEND=axum VITE_SUPPORT_DATA_BACKEND=axum
    docker compose up -d --build web worker beat
    for service in web worker; do wait_healthy "$service"; done
    docker compose exec -T web python manage.py migrate --noinput
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ./scripts/verify-axum-runtime.sh
    ;;
  support-rollback)
    set_profile SUPPORT_DATA_BACKEND=django VITE_SUPPORT_DATA_BACKEND=django
    docker compose up -d --build frontend realtime web worker beat
    for service in frontend realtime web worker; do wait_healthy "$service"; done
    ;;
  attachments-shadow)
    secret=$(read_env MEDIA_TOKEN_SHARED_SECRET)
    [ ${#secret} -ge 32 ] || fail "MEDIA_TOKEN_SHARED_SECRET must contain at least 32 characters"
    [ "$secret" != "replace-with-at-least-32-random-characters" ] || fail "Replace the example MEDIA_TOKEN_SHARED_SECRET first"
    set_profile CHAT_COMMAND_BACKEND=django CHAT_ATTACHMENT_BACKEND=sqlx_shadow VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_ATTACHMENT_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    prepare_chat_data_plane
    docker compose up -d --build realtime
    wait_healthy realtime
    ;;
  attachments)
    secret=$(read_env MEDIA_TOKEN_SHARED_SECRET)
    [ ${#secret} -ge 32 ] || fail "MEDIA_TOKEN_SHARED_SECRET must contain at least 32 characters"
    [ "$secret" != "replace-with-at-least-32-random-characters" ] || fail "Replace the example MEDIA_TOKEN_SHARED_SECRET first"
    set_profile CHAT_COMMAND_BACKEND=django CHAT_ATTACHMENT_BACKEND=axum CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_COMMAND_BACKEND=django VITE_CHAT_ATTACHMENT_BACKEND=axum VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    prepare_chat_data_plane
    docker compose up -d --build frontend realtime
    for service in frontend realtime; do wait_healthy "$service"; done
    ./scripts/verify-axum-runtime.sh
    ;;
  attachments-rollback)
    set_profile CHAT_ATTACHMENT_BACKEND=django CHAT_CONVERSATION_COMMAND_BACKEND=django VITE_CHAT_ATTACHMENT_BACKEND=django VITE_CHAT_CONVERSATION_COMMAND_BACKEND=django
    docker compose up -d --build frontend realtime web
    for service in frontend realtime web; do wait_healthy "$service"; done
    ;;
  outbox)
    set_profile REALTIME_DURABLE_BACKEND=nats REALTIME_OUTBOX_PUBLISHER=axum
    docker compose up -d --build realtime web worker beat
    for service in realtime web worker; do wait_healthy "$service"; done
    ./scripts/verify-axum-runtime.sh
    ;;
  outbox-rollback)
    set_profile REALTIME_OUTBOX_PUBLISHER=celery
    docker compose up -d --build realtime web worker beat
    for service in realtime web worker; do wait_healthy "$service"; done
    ;;
  media-shadow)
    set_profile MEDIA_PROCESSING_BACKEND=rust_shadow
    docker compose up -d --build web worker beat
    for service in web worker; do wait_healthy "$service"; done
    docker compose exec -T web python manage.py migrate --noinput
    ./scripts/generate-media-worker-lockfile.sh
    docker compose --profile rust-media up -d --build media-worker
    wait_healthy media-worker
    ;;
  media)
    set_profile MEDIA_PROCESSING_BACKEND=rust MEDIA_WORKER_DJANGO_FALLBACK_ENABLED=False
    docker compose up -d --build web worker beat
    for service in web worker; do wait_healthy "$service"; done
    docker compose exec -T web python manage.py migrate --noinput
    ./scripts/generate-media-worker-lockfile.sh
    docker compose --profile rust-media up -d --build media-worker
    wait_healthy media-worker
    ;;
  media-rollback)
    set_profile MEDIA_PROCESSING_BACKEND=django
    docker compose up -d --build web worker beat
    for service in web worker; do wait_healthy "$service"; done
    docker compose --profile rust-media stop media-worker || true
    ;;
  final|efficient)
    secret=$(read_env MEDIA_TOKEN_SHARED_SECRET)
    [ ${#secret} -ge 32 ] || fail "MEDIA_TOKEN_SHARED_SECRET must contain at least 32 characters"
    [ "$secret" != "replace-with-at-least-32-random-characters" ] || fail "Replace the example MEDIA_TOKEN_SHARED_SECRET first"
    set_profile AXUM_DATA_PLANE_REQUIRED=True MEDIA_PROCESSING_BACKEND=rust MEDIA_WORKER_DJANGO_FALLBACK_ENABLED=False REALTIME_DURABLE_BACKEND=nats REALTIME_OUTBOX_PUBLISHER=axum REALTIME_EPHEMERAL_BACKEND=nats REALTIME_PRESENCE_BACKEND=local REALTIME_CONNECTION_OWNERSHIP_BACKEND=local DATABASE_RUNTIME_ENDPOINT=pgbouncer CHAT_READ_BACKEND=sqlx CHAT_COMMAND_BACKEND=axum CHAT_INTERACTION_BACKEND=axum CHAT_MESSAGE_MUTATION_BACKEND=axum CHAT_CALL_RUNTIME_BACKEND=axum CHAT_ATTACHMENT_BACKEND=axum CHAT_CONVERSATION_COMMAND_BACKEND=axum SUPPORT_DATA_BACKEND=axum VITE_CHAT_COMMAND_BACKEND=axum VITE_CHAT_INTERACTION_BACKEND=axum VITE_CHAT_MESSAGE_MUTATION_BACKEND=axum VITE_CHAT_CALL_RUNTIME_BACKEND=axum VITE_CHAT_ATTACHMENT_BACKEND=axum VITE_CHAT_CONVERSATION_COMMAND_BACKEND=axum VITE_SUPPORT_DATA_BACKEND=axum VITE_CHAT_READ_BACKEND=sqlx DJANGO_SERVER=granian GRANIAN_WORKERS=1 SQLX_MIN_CONNECTIONS=1 SQLX_MAX_CONNECTIONS=4 SQLX_ACQUIRE_TIMEOUT_MS=1500 SQLX_IDLE_TIMEOUT_SECONDS=60 SQLX_MAX_LIFETIME_SECONDS=900 REALTIME_HTTP_READ_CONCURRENCY=24 REALTIME_HTTP_WRITE_CONCURRENCY=12 REALTIME_HTTP_REQUEST_TIMEOUT_MS=10000 REALTIME_HTTP_MAX_BODY_BYTES=1048576
    ./scripts/generate-realtime-lockfile.sh
    ./scripts/generate-media-worker-lockfile.sh
    docker compose --profile rust-media up -d --build web worker beat
    for service in web worker; do wait_healthy "$service"; done
    docker compose exec -T web python manage.py migrate --noinput
    docker compose --profile rust-media up -d --build frontend realtime media-worker nginx
    for service in frontend realtime media-worker nginx; do wait_healthy "$service"; done
    ./scripts/check-final-efficiency.sh
    ./scripts/verify-axum-runtime.sh
    ;;
  commands|hot-paths)
    secret=$(read_env MEDIA_TOKEN_SHARED_SECRET)
    [ ${#secret} -ge 32 ] || fail "MEDIA_TOKEN_SHARED_SECRET must contain at least 32 characters"
    [ "$secret" != "replace-with-at-least-32-random-characters" ] || fail "Replace the example MEDIA_TOKEN_SHARED_SECRET first"
    central=$(read_env CENTRAL_PAYMENTS_ENABLED)
    case "$central" in True|true|1|yes|on) fail "Axum command cutover is blocked while CENTRAL_PAYMENTS_ENABLED is active";; esac
    set_profile MEDIA_PROCESSING_BACKEND=rust MEDIA_WORKER_DJANGO_FALLBACK_ENABLED=False REALTIME_DURABLE_BACKEND=nats REALTIME_OUTBOX_PUBLISHER=axum REALTIME_EPHEMERAL_BACKEND=nats REALTIME_PRESENCE_BACKEND=legacy_redis REALTIME_CONNECTION_OWNERSHIP_BACKEND=local DATABASE_RUNTIME_ENDPOINT=pgbouncer CHAT_READ_BACKEND=sqlx CHAT_COMMAND_BACKEND=axum CHAT_INTERACTION_BACKEND=axum CHAT_MESSAGE_MUTATION_BACKEND=axum CHAT_CALL_RUNTIME_BACKEND=axum CHAT_ATTACHMENT_BACKEND=axum CHAT_CONVERSATION_COMMAND_BACKEND=axum SUPPORT_DATA_BACKEND=axum VITE_CHAT_COMMAND_BACKEND=axum VITE_CHAT_INTERACTION_BACKEND=axum VITE_CHAT_MESSAGE_MUTATION_BACKEND=axum VITE_CHAT_CALL_RUNTIME_BACKEND=axum VITE_CHAT_ATTACHMENT_BACKEND=axum VITE_CHAT_CONVERSATION_COMMAND_BACKEND=axum VITE_SUPPORT_DATA_BACKEND=axum VITE_CHAT_READ_BACKEND=sqlx DJANGO_SERVER=granian
    # Apply Django-owned schema changes before exposing the universal Rust route.
    prepare_chat_data_plane
    ./scripts/generate-media-worker-lockfile.sh
    docker compose --profile rust-media up -d --build frontend realtime media-worker
    for service in frontend realtime media-worker; do wait_healthy "$service"; done
    ./scripts/verify-axum-runtime.sh
    ;;
esac

show_status
