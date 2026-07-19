#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }

users="${1:-100}"
run_id="${2:-run-$(date -u +%Y%m%d%H%M%S)}"
output="${3:-loadtests/data/users.json}"
container_output="/tmp/realtime-load-users-${run_id}.json"
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)

mkdir -p "$(dirname "$output")"
chmod 700 "$(dirname "$output")" 2>/dev/null || true
"${compose[@]}" exec -T -e ALLOW_LOAD_TEST_DATA=true web \
  python manage.py prepare_realtime_load_test \
    --users "$users" --run-id "$run_id" --output "$container_output" --confirm --force
web_cid="$("${compose[@]}" ps -q web)"
[[ -n "$web_cid" ]] || { echo "Web container is not running." >&2; exit 1; }
docker cp "${web_cid}:${container_output}" "$output"
"${compose[@]}" exec -T web rm -f "$container_output"
chmod 600 "$output" 2>/dev/null || true
cat <<EOF
Load-test data prepared.
Run ID: $run_id
Credential file: $output
Securely copy this file to the external load generator. Delete both copies after testing.
EOF
