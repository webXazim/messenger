#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
compose=(docker compose)

[[ -f secrets/realtime-private.pem ]] || {
  echo "Missing secrets/realtime-private.pem; run ./scripts/generate-realtime-keys.sh" >&2
  exit 1
}
[[ -f secrets/realtime-public.pem ]] || {
  echo "Missing secrets/realtime-public.pem; run ./scripts/generate-realtime-keys.sh" >&2
  exit 1
}

"${compose[@]}" config >/dev/null
"${compose[@]}" build realtime web frontend
"${compose[@]}" run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 web python manage.py migrate
"${compose[@]}" run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 web python manage.py migrate --check
"${compose[@]}" run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 web python manage.py check
"${compose[@]}" up -d postgres redis realtime web frontend nginx

for _ in $(seq 1 30); do
  if "${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/live >/dev/null
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats
printf '\nAxum runtime verification passed.\n'
