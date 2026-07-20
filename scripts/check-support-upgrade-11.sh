#!/usr/bin/env bash
set -euo pipefail
./scripts/support-predeploy.sh
python manage.py test apps.support.tests_production_rollout
echo "All Support Chat Upgrade 11 checks passed."
