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
applications=(web worker beat realtime frontend nginx)
all_services=("${infrastructure[@]}" "${applications[@]}")
build_services=(pgbouncer web worker beat realtime frontend)
rollback_services=(pgbouncer web worker beat realtime frontend)

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

for service in "${infrastructure[@]}" web realtime frontend nginx; do
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
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats
printf '\n'
"${compose[@]}" ps

cutover_complete=1
trap - EXIT
printf '\nProduction cutover passed. Rollback image references: %s\n' "$rollback_file"
