#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
compose=(docker compose)
if [[ -f .env ]] && grep -Eiq '^MESSENGER_ENVIRONMENT=production([[:space:]]*)$' .env; then
  compose+=(--env-file .env -f docker-compose.yml -f docker-compose.production.yml)
fi
cutover_complete=0

restore_background_services() {
  status=$?
  if [[ $cutover_complete -ne 1 ]]; then
    "${compose[@]}" up -d worker beat >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap restore_background_services EXIT

[[ -f .env ]] || { echo "Missing .env" >&2; exit 1; }
if grep -Eiq '^MESSENGER_ENVIRONMENT=production([[:space:]]*)$' .env; then
  bash ./scripts/production-readiness.sh --preflight
fi
bash ./scripts/generate-realtime-lockfile.sh
[[ -f secrets/realtime-private.pem ]] || { echo "Missing realtime private key; run ./scripts/generate-realtime-keys.sh" >&2; exit 1; }
[[ -f secrets/realtime-public.pem ]] || { echo "Missing realtime public key; run ./scripts/generate-realtime-keys.sh" >&2; exit 1; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_file=".axum-rollback-images-${stamp}.env"
: > "$rollback_file"
for service in web frontend; do
  image_id="$("${compose[@]}" images -q "$service" 2>/dev/null | head -n1 || true)"
  if [[ -n "$image_id" ]]; then
    tag="cs-messenger-${service}:rollback-${stamp}"
    docker image tag "$image_id" "$tag"
    printf '%s=%s\n' "${service^^}_ROLLBACK_IMAGE" "$tag" >> "$rollback_file"
  fi
done

# Free nonessential application memory while compiling on a small VPS.
"${compose[@]}" stop worker beat >/dev/null 2>&1 || true

# Build sequentially; the Rust Dockerfile also limits Cargo to one job.
"${compose[@]}" build web
"${compose[@]}" build frontend
"${compose[@]}" build realtime

# Create a verified database rollback point before applying schema changes.
# Starting only the data services also supports deployment from a stopped stack.
"${compose[@]}" up -d postgres redis
bash ./scripts/backup-postgres.sh

"${compose[@]}" run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 web python manage.py migrate --noinput
"${compose[@]}" run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 web python manage.py migrate --check
"${compose[@]}" run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 web python manage.py check --deploy

# Replace services only after every build, validation, migration, and backup has succeeded.
"${compose[@]}" up -d --remove-orphans postgres redis web realtime frontend nginx worker beat

for _ in $(seq 1 45); do
  if "${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null
"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T web python manage.py check --deploy
"${compose[@]}" exec -T web python manage.py check_realtime_pipeline
if grep -Eiq '^TURN_PROVIDER=cloudflare([[:space:]]*)$' .env; then
  "${compose[@]}" exec -T web python manage.py check_call_readiness --probe
fi
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats
printf '\n'
"${compose[@]}" ps
cutover_complete=1
trap - EXIT
printf '\nAxum cutover passed. Image references were recorded in %s; keep the previous complete release directory for configuration rollback\n' "$rollback_file"
