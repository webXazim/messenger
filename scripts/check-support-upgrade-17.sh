#!/usr/bin/env bash
set -euo pipefail
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.support.tests_invitation_delivery apps.support.tests_baseline
(
  cd frontend
  npm run typecheck
  npm run build
)
