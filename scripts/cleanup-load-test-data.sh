#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
run_id="${1:-}"
credential_file="${2:-loadtests/data/users.json}"
[[ -n "$run_id" ]] || { echo "Usage: $0 <run-id> [credential-file]" >&2; exit 2; }
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
"${compose[@]}" exec -T -e ALLOW_LOAD_TEST_DATA=true web \
  python manage.py cleanup_realtime_load_test --run-id "$run_id" --confirm
rm -f -- "$credential_file"
echo "Temporary load-test data removed."
