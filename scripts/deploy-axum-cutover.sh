#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

fail() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ -f .env ]] || fail "Missing .env"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
infrastructure=(postgres redis nats pgbouncer)
applications=(web worker beat realtime media-worker frontend nginx)
all_services=("${infrastructure[@]}" "${applications[@]}")
build_services=(pgbouncer web worker beat realtime media-worker frontend)
rollback_services=(pgbouncer web worker beat realtime media-worker frontend)

wait_healthy() {
  local service="$1" attempts=0 cid status
  while (( attempts < 60 )); do
    cid="$("${compose[@]}" ps -q "$service")"
    if [[ -n "$cid" ]]; then
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
      [[ "$status" == "healthy" ]] && return 0
      if [[ "$status" == "exited" || "$status" == "dead" ]]; then
        "${compose[@]}" logs --tail=150 "$service" >&2 || true
        fail "$service stopped during startup"
      fi
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  "${compose[@]}" logs --tail=150 "$service" >&2 || true
  fail "$service did not become healthy within 120 seconds"
}

wait_running() {
  local service="$1" attempts=0 cid status
  while (( attempts < 30 )); do
    cid="$("${compose[@]}" ps -q "$service")"
    if [[ -n "$cid" ]]; then
      status="$(docker inspect -f '{{.State.Status}}' "$cid")"
      [[ "$status" == "running" ]] && return 0
      [[ "$status" == "exited" || "$status" == "dead" ]] && break
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  "${compose[@]}" logs --tail=150 "$service" >&2 || true
  fail "$service is not running"
}

cutover_complete=0
restore_background_services() {
  local status=$?
  if [[ $cutover_complete -ne 1 ]]; then
    "${compose[@]}" up -d worker beat >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap restore_background_services EXIT

if grep -Eiq '^MESSENGER_ENVIRONMENT=production([[:space:]]*)$' .env; then
  bash ./scripts/production-readiness.sh --preflight
fi
bash ./scripts/generate-realtime-lockfile.sh
bash ./scripts/generate-media-worker-lockfile.sh
[[ -s secrets/realtime-private.pem ]] || fail "Missing realtime private key; run ./scripts/generate-realtime-keys.sh"
[[ -s secrets/realtime-public.pem ]] || fail "Missing realtime public key; run ./scripts/generate-realtime-keys.sh"
"${compose[@]}" config --quiet

info "Rendering and parsing the NATS configuration"
"${compose[@]}" run --rm --no-deps -e NATS_CONFIG_TEST_ONLY=1 nats

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_file=".axum-rollback-images-${stamp}.env"
: > "$rollback_file"
for service in "${rollback_services[@]}"; do
  image_id="$("${compose[@]}" images -q "$service" 2>/dev/null | head -n1 || true)"
  if [[ -n "$image_id" ]]; then
    tag="cs-messenger-${service}:rollback-${stamp}"
    docker image tag "$image_id" "$tag"
    printf '%s=%s\n' "${service^^}_ROLLBACK_IMAGE" "$tag" >> "$rollback_file"
  fi
done

# Preserve RAM on small VPS hosts while compiling. Each service is built
# separately so Compose never runs the Python, Rust, and Node builds together.
"${compose[@]}" stop worker beat >/dev/null 2>&1 || true
build_args=()
if [[ "${DEPLOY_PULL_BASE_IMAGES:-0}" =~ ^(1|true|yes|on)$ ]]; then
  build_args+=(--pull)
fi
for service in "${build_services[@]}"; do
  info "Building $service"
  COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build "${build_args[@]}" "$service"
done

info "Starting PostgreSQL, Redis, NATS, and PgBouncer"
"${compose[@]}" up -d "${infrastructure[@]}"
for service in "${infrastructure[@]}"; do
  wait_healthy "$service"
done

info "Creating a verified database rollback point"
bash ./scripts/backup-postgres.sh

run_web=("${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0)
info "Checking model and migration consistency"
"${run_web[@]}" -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py makemigrations --check --dry-run
"${run_web[@]}" -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py migrate --noinput
"${run_web[@]}" -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py migrate --check
"${run_web[@]}" web python manage.py check --deploy

info "Ensuring the NATS JetStream resources exist"
"${run_web[@]}" web python manage.py ensure_nats_stream

info "Replacing the complete production stack"
if ! "${compose[@]}" up -d --remove-orphans "${all_services[@]}"; then
  "${compose[@]}" logs --tail=150 nats realtime >&2 || true
  fail "production stack replacement failed"
fi

# Git can replace the inode behind Nginx's bind-mounted configuration. Always
# recreate Nginx so it reads the configuration from the deployed release.
"${compose[@]}" up -d --force-recreate --no-deps nginx

for service in "${infrastructure[@]}" web realtime media-worker frontend nginx; do
  wait_healthy "$service"
done
wait_running worker
wait_running beat

"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T web python manage.py check --deploy
"${compose[@]}" exec -T web python manage.py migrate --check
"${compose[@]}" exec -T web python manage.py check_realtime_pipeline
"${compose[@]}" exec -T worker celery -A config inspect ping --timeout=10
if grep -Eiq '^TURN_PROVIDER=cloudflare([[:space:]]*)$' .env; then
  "${compose[@]}" exec -T web python manage.py check_call_readiness --probe
fi
runtime_state="$("${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats)"
printf '%s\n' "$runtime_state"
expected_command_backend="$(grep -E '^CHAT_COMMAND_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
expected_interaction_backend="$(grep -E '^CHAT_INTERACTION_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
expected_message_mutation_backend="$(grep -E '^CHAT_MESSAGE_MUTATION_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
expected_call_runtime_backend="$(grep -E '^CHAT_CALL_RUNTIME_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
expected_attachment_backend="$(grep -E '^CHAT_ATTACHMENT_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
expected_conversation_command_backend="$(grep -E '^CHAT_CONVERSATION_COMMAND_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
expected_read_backend="$(grep -E '^CHAT_READ_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
if [[ "$expected_command_backend" == "axum" ]]; then
  grep -q '"chat_command_backend":"axum"' <<<"$runtime_state" || fail "Axum command backend was requested but is not active"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state" || fail "Axum commands require a healthy SQLx pool"
  call_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/v1/chat-fast/calls/recent/)"
  [[ "$call_route_status" == "401" ]] || fail "Axum call API probe expected HTTP 401, got $call_route_status"
fi
if [[ "$expected_interaction_backend" == "axum" ]]; then
  grep -q '"chat_interaction_backend":"axum"' <<<"$runtime_state" || fail "Axum interaction backend was requested but is not active"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state" || fail "Axum interactions require a healthy SQLx pool"
  interaction_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:9000/api/v1/chat-fast/conversations/00000000-0000-0000-0000-000000000000/mark-read/)"
  [[ "$interaction_route_status" == "401" ]] || fail "Axum interaction API probe expected HTTP 401, got $interaction_route_status"
  expected_frontend_interaction_backend="$(grep -E '^VITE_CHAT_INTERACTION_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ "$expected_frontend_interaction_backend" == "axum" ]] || fail "CHAT_INTERACTION_BACKEND=axum also requires VITE_CHAT_INTERACTION_BACKEND=axum and a rebuilt frontend image"
fi
if [[ "$expected_message_mutation_backend" == "axum" ]]; then
  grep -q '"chat_message_mutation_backend":"axum"' <<<"$runtime_state" || fail "Axum message mutation backend was requested but is not active"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state" || fail "Axum message mutations require a healthy SQLx pool"
  mutation_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:9000/api/v1/chat-fast/messages/00000000-0000-0000-0000-000000000000/restore/)"
  [[ "$mutation_route_status" == "401" ]] || fail "Axum message mutation API probe expected HTTP 401, got $mutation_route_status"
  expected_frontend_message_mutation_backend="$(grep -E '^VITE_CHAT_MESSAGE_MUTATION_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ "$expected_frontend_message_mutation_backend" == "axum" ]] || fail "CHAT_MESSAGE_MUTATION_BACKEND=axum also requires VITE_CHAT_MESSAGE_MUTATION_BACKEND=axum and a rebuilt frontend image"
fi
if [[ "$expected_call_runtime_backend" == "axum" ]]; then
  grep -q '"chat_call_runtime_backend":"axum"' <<<"$runtime_state" || fail "Axum call runtime backend was requested but is not active"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state" || fail "Axum call runtime requires a healthy SQLx pool"
  grep -q '"ephemeral_backend":"nats"' <<<"$runtime_state" || fail "Axum call runtime requires Core NATS as its ephemeral transport"
  grep -q '"ephemeral_ready":true' <<<"$runtime_state" || fail "Axum call runtime Core NATS transport is not ready"
  call_runtime_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:9000/api/v1/chat-fast/calls/00000000-0000-0000-0000-000000000000/heartbeat/)"
  [[ "$call_runtime_route_status" == "401" ]] || fail "Axum call runtime API probe expected HTTP 401, got $call_runtime_route_status"
  expected_frontend_call_runtime_backend="$(grep -E '^VITE_CHAT_CALL_RUNTIME_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ "$expected_frontend_call_runtime_backend" == "axum" ]] || fail "CHAT_CALL_RUNTIME_BACKEND=axum also requires VITE_CHAT_CALL_RUNTIME_BACKEND=axum and a rebuilt frontend image"
  expected_ephemeral_backend="$(grep -E '^REALTIME_EPHEMERAL_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ "$expected_ephemeral_backend" == "nats" ]] || fail "CHAT_CALL_RUNTIME_BACKEND=axum requires REALTIME_EPHEMERAL_BACKEND=nats for multi-node signaling"
fi
if [[ "$expected_attachment_backend" == "axum" ]]; then
  grep -q '"chat_attachment_backend":"axum"' <<<"$runtime_state" || fail "Axum attachment backend was requested but is not active"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state" || fail "Axum attachments require a healthy SQLx pool"
  media_secret="$(grep -E '^MEDIA_TOKEN_SHARED_SECRET=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ ${#media_secret} -ge 32 ]] || fail "CHAT_ATTACHMENT_BACKEND=axum requires MEDIA_TOKEN_SHARED_SECRET with at least 32 characters"
  [[ "$media_secret" != "replace-with-at-least-32-random-characters" ]] || fail "Replace the example MEDIA_TOKEN_SHARED_SECRET first"
  attachment_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/v1/chat-fast/attachments/00000000-0000-0000-0000-000000000000/)"
  [[ "$attachment_route_status" == "401" ]] || fail "Axum attachment API probe expected HTTP 401, got $attachment_route_status"
  expected_frontend_attachment_backend="$(grep -E '^VITE_CHAT_ATTACHMENT_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ "$expected_frontend_attachment_backend" == "axum" ]] || fail "CHAT_ATTACHMENT_BACKEND=axum also requires VITE_CHAT_ATTACHMENT_BACKEND=axum and a rebuilt frontend image"
fi
if [[ "$expected_conversation_command_backend" == "axum" ]]; then
  grep -q '"chat_conversation_command_backend":"axum"' <<<"$runtime_state" || fail "Axum conversation command backend was requested but is not active"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state" || fail "Axum conversation commands require a healthy SQLx pool"
  conversation_command_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:9000/api/v1/chat-fast/blocks/)"
  [[ "$conversation_command_status" == "401" ]] || fail "Axum conversation command API probe expected HTTP 401, got $conversation_command_status"
  expected_frontend_conversation_command_backend="$(grep -E '^VITE_CHAT_CONVERSATION_COMMAND_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ "$expected_frontend_conversation_command_backend" == "axum" ]] || fail "CHAT_CONVERSATION_COMMAND_BACKEND=axum also requires VITE_CHAT_CONVERSATION_COMMAND_BACKEND=axum and a rebuilt frontend image"
fi
if [[ "$expected_read_backend" == "sqlx" ]]; then
  grep -q '"chat_read_backend":"sqlx"' <<<"$runtime_state" || fail "SQLx read backend was requested but is not active"
  read_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/v1/chat-fast/conversations/)"
  [[ "$read_route_status" == "401" ]] || fail "Axum conversation read probe expected HTTP 401, got $read_route_status"
  expected_frontend_read_backend="$(grep -E '^VITE_CHAT_READ_BACKEND=' .env | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ "$expected_frontend_read_backend" == "sqlx" ]] || fail "CHAT_READ_BACKEND=sqlx also requires VITE_CHAT_READ_BACKEND=sqlx and a rebuilt frontend image"
fi
printf '\n'
"${compose[@]}" ps

cutover_complete=1
trap - EXIT
printf '\nProduction cutover passed. Rollback image references: %s\n' "$rollback_file"
