#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
[[ "${FAILURE_DRILL_ACK:-}" == "I_UNDERSTAND_THIS_INTERRUPTS_USERS" ]] || {
  echo "Set FAILURE_DRILL_ACK=I_UNDERSTAND_THIS_INTERRUPTS_USERS to run a controlled restart drill." >&2
  exit 1
}
service="${1:-}"
case "$service" in realtime|redis|web) ;; *) echo "Usage: $0 {realtime|redis|web}" >&2; exit 2 ;; esac
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)

echo "Pre-drill health check..."
./scripts/operational-health.sh
before="$("${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats 2>/dev/null || true)"
echo "Restarting $service..."
"${compose[@]}" restart "$service"

case "$service" in
  realtime)
    timeout 90 bash -c 'until docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; do sleep 2; done'
    ;;
  redis)
    timeout 120 bash -c 'until docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml exec -T redis redis-cli ping 2>/dev/null | grep -q PONG && docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; do sleep 2; done'
    ;;
  web)
    timeout 90 bash -c 'until docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml exec -T web python manage.py check --deploy >/dev/null 2>&1; do sleep 2; done'
    ;;
esac

echo "Publishing a durable canary after restart..."
"${compose[@]}" exec -T web python manage.py emit_realtime_canary --timeout 30
./scripts/operational-health.sh
after="$("${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats 2>/dev/null || true)"
printf 'Before: %s\nAfter:  %s\n' "$before" "$after"
echo "$service failure drill passed. Run the external reconnect test during a maintenance window for client-visible verification."
