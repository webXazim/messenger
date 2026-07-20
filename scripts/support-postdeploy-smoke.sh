#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

echo "Checking application health at $BASE_URL"
curl --fail --silent --show-error "$BASE_URL/" >/dev/null
python manage.py check_support_production_readiness --strict

echo "Manual smoke test checklist:"
echo "  [ ] Existing Support Inbox opens"
echo "  [ ] Existing conversation sends and receives messages"
echo "  [ ] Website widget creates a conversation"
echo "  [ ] Agent assignment and routing work"
echo "  [ ] Knowledge search returns authorized articles"
echo "  [ ] SLA state appears and updates"
echo "  [ ] Analytics loads after backfill"
echo "  [ ] Automation execution log records a safe test rule"
echo "  [ ] Messenger conversations remain isolated"
