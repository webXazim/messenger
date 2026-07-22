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
media_backend=""
if [[ -f .env ]]; then
  media_backend="$(grep -E '^MEDIA_PROCESSING_BACKEND=' .env | tail -1 | cut -d= -f2-)"
fi
if [[ "$media_backend" == "rust" || "$media_backend" == "rust_shadow" ]]; then
  ./scripts/generate-media-worker-lockfile.sh
  COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" --profile rust-media build media-worker
fi
"${compose[@]}" up -d postgres redis nats pgbouncer
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py migrate
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py migrate --check
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 web python manage.py check
"${compose[@]}" run --rm --no-deps -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 web python manage.py ensure_nats_stream
"${compose[@]}" up -d postgres pgbouncer redis nats realtime web worker beat frontend nginx
if [[ "$media_backend" == "rust" || "$media_backend" == "rust_shadow" ]]; then
  "${compose[@]}" --profile rust-media up -d media-worker
fi

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
if [[ -f .env ]] && grep -Eq '^REALTIME_OUTBOX_PUBLISHER=axum([[:space:]]*)$' .env; then
  grep -q '"outbox_publisher":"axum"' <<<"$runtime_state"
  grep -q '"direct_outbox_ready":true' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
fi
if [[ -f .env ]] && grep -Eq '^CHAT_COMMAND_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"chat_command_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  message_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:9000/api/v1/chat-fast/conversations/00000000-0000-0000-0000-000000000000/messages/)"
  [[ "$message_route_status" == "401" ]]
  call_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/v1/chat-fast/calls/recent/)"
  [[ "$call_route_status" == "401" ]]
fi
if [[ -f .env ]] && grep -Eq '^CHAT_INTERACTION_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"chat_interaction_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  interaction_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:9000/api/v1/chat-fast/conversations/00000000-0000-0000-0000-000000000000/mark-read/)"
  [[ "$interaction_route_status" == "401" ]]
fi
if [[ -f .env ]] && grep -Eq '^CHAT_MESSAGE_MUTATION_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"chat_message_mutation_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  mutation_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:9000/api/v1/chat-fast/messages/00000000-0000-0000-0000-000000000000/restore/)"
  [[ "$mutation_route_status" == "401" ]]
fi
if [[ -f .env ]] && grep -Eq '^CHAT_CALL_RUNTIME_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"chat_call_runtime_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  grep -q '"ephemeral_backend":"nats"' <<<"$runtime_state"
  grep -q '"ephemeral_ready":true' <<<"$runtime_state"
  call_runtime_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:9000/api/v1/chat-fast/calls/00000000-0000-0000-0000-000000000000/heartbeat/)"
  [[ "$call_runtime_route_status" == "401" ]]
fi
if [[ -f .env ]] && grep -Eq '^CHAT_ATTACHMENT_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"chat_attachment_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  media_secret="$(grep -E '^MEDIA_TOKEN_SHARED_SECRET=' .env | tail -1 | cut -d= -f2-)"
  [[ ${#media_secret} -ge 32 ]]
  [[ "$media_secret" != "replace-with-at-least-32-random-characters" ]]
  attachment_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/v1/chat-fast/attachments/00000000-0000-0000-0000-000000000000/)"
  [[ "$attachment_route_status" == "401" ]]
  attachment_token_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:9000/api/v1/chat-fast/attachments/00000000-0000-0000-0000-000000000000/media-token/)"
  [[ "$attachment_token_status" == "401" ]]
fi
if [[ -f .env ]] && grep -Eq '^CHAT_CONVERSATION_COMMAND_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"chat_conversation_command_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  conversation_command_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:9000/api/v1/chat-fast/blocks/)"
  [[ "$conversation_command_status" == "401" ]]
fi
if [[ -f .env ]] && grep -Eq '^SUPPORT_DATA_BACKEND=axum([[:space:]]*)$' .env; then
  grep -q '"support_data_backend":"axum"' <<<"$runtime_state"
  grep -q '"sqlx_enabled":true' <<<"$runtime_state"
  grep -q '"ephemeral_backend":"nats"' <<<"$runtime_state"
  grep -q '"ephemeral_ready":true' <<<"$runtime_state"
  support_route_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/v1/support-fast/conversations/)"
  [[ "$support_route_status" == "401" ]]
  support_widget_status="$("${compose[@]}" exec -T realtime curl -sS -o /dev/null -w '%{http_code}' -X OPTIONS -H 'Origin: https://invalid.example' http://127.0.0.1:9000/api/v1/support-fast/widget/00000000-0000-0000-0000-000000000000/sessions/00000000-0000-0000-0000-000000000000/messages/)"
  [[ "$support_widget_status" == "204" ]]
  support_migrations="$("${compose[@]}" exec -T web python manage.py showmigrations support --plan)"
  grep -Eq '\[X\].*0024_support_data_plane_jobs' <<<"$support_migrations"
fi
if [[ -f .env ]] && grep -Eiq '^(CHAT_COMMAND_BACKEND|CHAT_ATTACHMENT_BACKEND|CHAT_CONVERSATION_COMMAND_BACKEND)=axum([[:space:]]*)$' .env; then
  chat_migrations="$("${compose[@]}" exec -T web python manage.py showmigrations chat --plan)"
  grep -Eq '\[X\].*0023_chat_data_plane_jobs' <<<"$chat_migrations"
fi
if [[ -f .env ]] && grep -Eq '^CHAT_READ_BACKEND=sqlx([[:space:]]*)$' .env; then
  grep -q '"chat_read_backend":"sqlx"' <<<"$runtime_state"
fi

if [[ "$media_backend" == "rust" || "$media_backend" == "rust_shadow" ]]; then
  for _ in $(seq 1 30); do
    media_state="$("${compose[@]}" --profile rust-media ps --format json media-worker 2>/dev/null || true)"
    if grep -q 'healthy' <<<"$media_state"; then
      break
    fi
    sleep 2
  done
  "${compose[@]}" --profile rust-media exec -T media-worker /usr/local/bin/media-worker --healthcheck
  chat_migrations="$("${compose[@]}" exec -T web python manage.py showmigrations chat --plan)"
  grep -Eq '\[X\].*0024_rust_media_processing_jobs' <<<"$chat_migrations"
fi
printf '\nAxum runtime verification passed.\n'
