#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

compose=(docker compose -f docker-compose.yml)
"${compose[@]}" build web
"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e MESSENGER_ENVIRONMENT=test \
  -e MESSENGER_REQUIRE_SECURE_SETTINGS=False \
  -e MESSENGER_TEST_USE_LOCAL_SERVICES=True \
  -e DEBUG=False \
  -e SECRET_KEY=test-only-secret-key-that-is-not-used-in-production-0123456789 \
  -e DB_ENGINE=sqlite \
  -e RUN_MIGRATIONS=0 \
  -e CENTRAL_AUTH_ENABLED=False \
  -e CENTRAL_PAYMENTS_ENABLED=False \
  -e CENTRAL_ADMIN_ENABLED=False \
  -e CENTRAL_ACCESS_MODE=observe \
  -e CHAT_USE_R2_STORAGE=False \
  -e CHAT_USE_S3_STORAGE=False \
  -e AUTH_REQUIRE_EMAIL_VERIFICATION=False \
  -e EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend \
  web manage.py test --noinput
