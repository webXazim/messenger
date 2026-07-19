#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
compose=(docker compose)
production=0
if [[ -f .env ]] && grep -Eiq '^MESSENGER_ENVIRONMENT=production([[:space:]]*)$' .env; then
  compose+=(--env-file .env -f docker-compose.yml -f docker-compose.production.yml)
  production=1
fi

restore_background_services() {
  status=$?
  "${compose[@]}" up -d worker beat >/dev/null 2>&1 || true
  exit "$status"
}
trap restore_background_services EXIT

bash ./scripts/generate-realtime-lockfile.sh
"${compose[@]}" config >/dev/null

# Keep compile pressure predictable on the 2 GB VPS.
"${compose[@]}" stop worker beat >/dev/null 2>&1 || true
"${compose[@]}" build realtime
"${compose[@]}" build web
"${compose[@]}" build frontend

# These tests cover ticket/grant isolation, active-call grants, and Support
# message retry idempotency against the same Django code used in production.
"${compose[@]}" run --rm \
  -e RUN_MIGRATIONS=0 \
  -e RUN_COLLECTSTATIC=0 \
  web python manage.py test \
    apps.common.tests_realtime_auth \
    apps.support.tests.SupportFoundationTests.test_widget_message_client_temp_id_is_idempotent \
    apps.support.tests.SupportFoundationTests.test_team_message_client_temp_id_is_idempotent \
    --keepdb --noinput

# The schema change is backward-compatible and must be applied before the new
# web/frontend containers become active.
"${compose[@]}" run --rm \
  -e RUN_MIGRATIONS=0 \
  -e RUN_COLLECTSTATIC=0 \
  web python manage.py migrate --noinput
"${compose[@]}" run --rm \
  -e RUN_MIGRATIONS=0 \
  -e RUN_COLLECTSTATIC=0 \
  web python manage.py check --deploy

"${compose[@]}" up -d postgres redis web realtime frontend nginx worker beat

for _ in $(seq 1 30); do
  if "${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null
"${compose[@]}" exec -T web python manage.py migrate --check
"${compose[@]}" exec -T web python manage.py check --deploy
if (( production == 1 )); then
  "${compose[@]}" exec -T web python manage.py check_call_readiness --probe
  "${compose[@]}" exec -T web python manage.py check_support_readiness --fail-on-warning
fi

trap - EXIT
printf '\nAxum feature-parity verification passed.\n'
