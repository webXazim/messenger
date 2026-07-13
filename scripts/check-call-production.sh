#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)

echo "Running Django deployment checks..."
"${compose[@]}" exec -T web python manage.py check --deploy

echo "Checking calling and TURN configuration..."
"${compose[@]}" exec -T web python manage.py check_call_readiness --probe

echo "Checking frontend call-state and floating-video logic..."
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm run test:call)
else
  echo "npm is unavailable on this deployment host; frontend call tests were skipped here."
  echo "Run npm run test:call in CI or a release-validation environment."
fi

echo "Automated checks passed. Complete the real-network call checks documented in README.md."
