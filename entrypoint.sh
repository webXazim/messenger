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
  run_manage migrate --noinput
fi

if [ "$(id -u)" = "0" ]; then
  exec runuser -u appuser -- "$@"
fi
exec "$@"
