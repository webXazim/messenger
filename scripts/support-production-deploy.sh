#!/usr/bin/env bash
set -euo pipefail

echo "== Support Chat production deployment =="
./scripts/support-predeploy.sh

python manage.py migrate --noinput
python manage.py backfill_support_analytics --days "${SUPPORT_ANALYTICS_BACKFILL_DAYS:-90}"
python manage.py collectstatic --noinput
python manage.py check_support_production_readiness --strict

cat <<'EOF'
Deployment preparation completed.

Restart in this order:
1. Django web/API services
2. Celery workers
3. Celery beat
4. Realtime/Axum or Elixir services
5. Reverse proxy

Enable rollout flags one module at a time after smoke tests.
EOF
