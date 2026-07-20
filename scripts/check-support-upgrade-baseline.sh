#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python manage.py check
python manage.py check_support_baseline
python manage.py test apps.support.tests_baseline apps.support.api.tests_cursors
(
  cd frontend
  npm run check:support-baseline
  npm run test:support
)

echo "Support Chat upgrade baseline passed."
