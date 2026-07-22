#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
compose=(docker compose)
if [[ -f .env ]] && grep -Eiq '^MESSENGER_ENVIRONMENT=production([[:space:]]*)$' .env; then
  compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
fi

[[ -f secrets/realtime-private.pem ]] || {
  echo "Missing secrets/realtime-private.pem; run ./scripts/generate-realtime-keys.sh" >&2
  exit 1
}
[[ -f secrets/realtime-public.pem ]] || {
  echo "Missing secrets/realtime-public.pem; run ./scripts/generate-realtime-keys.sh" >&2
  exit 1
}

"${compose[@]}" config >/dev/null
for service in pgbouncer realtime web worker beat frontend; do
  COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build "$service"
done
"${compose[@]}" up -d postgres redis nats pgbouncer
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py migrate
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py migrate --check
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 web python manage.py check
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 web python manage.py ensure_nats_stream
"${compose[@]}" up -d postgres pgbouncer redis nats realtime web worker beat frontend nginx

for _ in $(seq 1 30); do
  if "${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/live >/dev/null
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null
runtime_state="$("${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats)"
printf '%s\n' "$runtime_state"
if [[ -f .env ]] && grep -Eq '^CHAT_COMMAND_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"chat_command_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  call_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/v1/chat-fast/calls/recent/)"
  [[ "$call_route_status" == "401" ]]
fi
if [[ -f .env ]] && grep -Eq '^CHAT_READ_BACKEND=sqlx([[:space:]]*)$' .env; then
  grep -q '"chat_read_backend":"sqlx"' <<<"$runtime_state"
fi
printf '\nAxum runtime verification passed.\n'
