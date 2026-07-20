#!/usr/bin/env bash
set -euo pipefail
python manage.py check
python manage.py migrate --check
python manage.py test apps.support.tests_knowledge apps.support.tests_knowledge_production
(cd frontend && npm run typecheck && npm run build)
