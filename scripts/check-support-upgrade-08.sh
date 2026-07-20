#!/usr/bin/env bash
set -euo pipefail
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test   apps.support.tests_sla_engine   apps.support.tests_service_operations   apps.support.tests_lifecycle   apps.support.tests_baseline
cd frontend
npm run typecheck
npm run check:support-baseline
npm run build
