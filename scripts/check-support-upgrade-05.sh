#!/usr/bin/env bash
set -euo pipefail
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.support.tests_routing apps.support.tests_baseline apps.support.tests_teams
cd frontend
npm run typecheck
npm run build
