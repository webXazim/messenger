#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
action="${1:-status}"
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
case "$action" in
  preflight)
    ./scripts/production-preflight.sh
    ./scripts/operational-health.sh
    "${compose[@]}" exec -T web python manage.py emit_realtime_canary --timeout 30
    ;;
  prepare)
    users="${2:-500}"
    ./scripts/prepare-load-test-data.sh "$users"
    cat <<TXT
Next, from an external load generator set LOADTEST_BASE_URL, LOADTEST_WS_URL and LOADTEST_ORIGIN, then run:
  ./scripts/run-load-test.sh smoke 10
  ./scripts/run-load-test.sh capacity $users
  ./scripts/run-load-test.sh mixed $(( users < 250 ? users : 250 ))
  ./scripts/run-load-test.sh reconnect $(( users < 250 ? users : 250 ))
Capture VPS metrics concurrently with scripts/capture-load-test-metrics.sh.
TXT
    ;;
  drill)
    for service in realtime nats pgbouncer postgres web redis; do
      FAILURE_DRILL_ACK=I_UNDERSTAND_THIS_INTERRUPTS_USERS ./scripts/failure-drill.sh "$service"
    done
    ;;
  analyze)
    dir="${2:-loadtests/results/final-suite}"
    target="${3:-500}"
    python3 scripts/analyze-load-test.py \
      --summary "$dir/realtime-capacity-${target}.json" \
      --mixed-summary "$dir/mixed-production-$(( target < 250 ? target : 250 )).json" \
      --server-metrics "$dir/vps-metrics.jsonl" \
      --postgres-audit "$dir/postgres-audit.json" \
      --query-plans "$dir/critical-query-plans.json" \
      --target-connections "$target" \
      --output "$dir/capacity-report.json"
    ;;
  status)
    ./scripts/operational-health.sh
    "${compose[@]}" exec -T nats wget -qO- 'http://127.0.0.1:8222/jsz?streams=true&consumers=true' | python3 -m json.tool
    ;;
  *) echo "Usage: $0 {preflight|prepare [users]|drill|analyze [result-dir] [target]|status}" >&2; exit 2 ;;
esac
