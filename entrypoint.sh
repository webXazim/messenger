#!/bin/sh
set -e

ensure_writable_media_dirs() {
  mkdir -p /app/staticfiles /app/media /app/private_media
  chown -R appuser:appuser /app/staticfiles /app/media /app/private_media
}

run_manage() {
  if [ "$(id -u)" = "0" ]; then
    runuser -u appuser -- python manage.py "$@"
  else
    python manage.py "$@"
  fi
}

if [ "$(id -u)" = "0" ]; then
  ensure_writable_media_dirs
fi

# Only the web service needs static collection. Workers and beat explicitly set
# RUN_COLLECTSTATIC=0 so multiple containers never race on startup.
if [ "${RUN_COLLECTSTATIC:-1}" = "1" ]; then
  run_manage collectstatic --noinput
fi

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  # Schema changes bypass transaction-pooled PgBouncer. Runtime traffic can be
  # switched independently through DATABASE_RUNTIME_ENDPOINT.
  DATABASE_MAINTENANCE_MODE=True \
  DATABASE_RUNTIME_ENDPOINT=postgres \
  DB_HOST="${DB_MIGRATION_HOST:-${DB_HOST:-postgres}}" \
  DB_PORT="${DB_MIGRATION_PORT:-${DB_PORT:-5432}}" \
    run_manage migrate --noinput
fi

# A fresh JetStream server has no streams. Create the durable chat stream before
# the HTTP server starts so Axum can become ready without waiting for the first
# user-generated event. This is enabled only for the web service.
if [ "${ENSURE_NATS_STREAM:-0}" = "1" ] && [ "${REALTIME_DURABLE_BACKEND:-nats}" = "nats" ]; then
  run_manage ensure_nats_stream
fi

if [ "$(id -u)" = "0" ]; then
  exec runuser -u appuser -- "$@"
fi
exec "$@"
