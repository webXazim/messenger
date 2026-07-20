#!/usr/bin/env sh
set -eu
python manage.py check
python manage.py test apps.support.tests_baseline apps.support.tests --keepdb
(cd frontend && npm run check:support-baseline && npm run check:support-design-system && npm run check:support-websites && npm run build)
