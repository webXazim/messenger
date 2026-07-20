#!/usr/bin/env bash
set -euo pipefail

echo "== Support Chat pre-deployment checks =="
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py check_support_baseline
python manage.py check_support_production_readiness --strict
python manage.py test \
  apps.support.tests_baseline \
  apps.support.tests_routing \
  apps.support.tests_lifecycle \
  apps.support.tests_sla_engine \
  apps.support.tests_analytics_aggregates \
  apps.support.tests_automation_security

(
  cd frontend
  npm run typecheck
  npm run check:support-baseline
  npm run build
)

echo "Pre-deployment checks passed."
