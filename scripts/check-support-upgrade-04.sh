#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python manage.py check
python manage.py migrate --check
python manage.py test apps.support.tests_teams apps.support.tests_baseline
(
  cd frontend
  npm ci
  npm run typecheck
  npm run build
)
