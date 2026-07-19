#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
identity="${1:-}"
conversation_id="${2:-}"
output_dir="${3:-loadtests/results/final-suite}"
[[ -n "$identity" ]] || {
  echo "Usage: $0 <support-owner-or-agent-email> [conversation-id] [output-dir]" >&2
  exit 2
}
[[ "${PERFORMANCE_PLAN_ACK:-}" == "RUN_EXPLAIN_ANALYZE" ]] || {
  echo "Set PERFORMANCE_PLAN_ACK=RUN_EXPLAIN_ANALYZE during a controlled low-traffic window." >&2
  exit 1
}

mkdir -p "$output_dir"
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)

"${compose[@]}" exec -T web \
  python manage.py audit_postgres_performance --json \
  > "$output_dir/postgres-audit.json"

plan_args=(
  python manage.py capture_critical_query_plans
  --user "$identity"
  --limit 50
  --analyze
  --json
)
if [[ -n "$conversation_id" ]]; then
  plan_args+=(--conversation-id "$conversation_id")
fi
"${compose[@]}" exec -T web "${plan_args[@]}" \
  > "$output_dir/critical-query-plans.json"

python3 - "$output_dir/postgres-audit.json" "$output_dir/critical-query-plans.json" <<'PY'
import json, sys
from pathlib import Path
audit=json.loads(Path(sys.argv[1]).read_text())
plans=json.loads(Path(sys.argv[2]).read_text())
if audit.get('passed') is not True:
    raise SystemExit('PostgreSQL audit did not pass.')
if plans.get('strict_passed') is not True:
    raise SystemExit('Critical query plan review has unresolved errors or warnings.')
if plans.get('analyzed') is not True or plans.get('skipped'):
    raise SystemExit('Critical query plan coverage is incomplete.')
print('PostgreSQL audit and critical EXPLAIN ANALYZE plans passed.')
PY

chmod 600 "$output_dir/postgres-audit.json" "$output_dir/critical-query-plans.json" 2>/dev/null || true
echo "Performance diagnostics written to $output_dir"
