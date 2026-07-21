#!/bin/sh
set -eu

server="${DJANGO_SERVER:-gunicorn}"
host="${DJANGO_BIND_HOST:-0.0.0.0}"
port="${PORT:-${DJANGO_PORT:-8000}}"

case "$server" in
  gunicorn)
    echo "Starting Django with Gunicorn (rollback-compatible mode)"
    exec gunicorn config.wsgi:application \
      --bind "${host}:${port}" \
      --workers "${GUNICORN_WORKERS:-2}" \
      --threads "${GUNICORN_THREADS:-2}" \
      --timeout "${GUNICORN_TIMEOUT:-30}" \
      --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
      --keep-alive "${GUNICORN_KEEP_ALIVE:-5}" \
      --max-requests "${GUNICORN_MAX_REQUESTS:-1000}" \
      --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-100}"
    ;;
  granian)
    echo "Starting Django with Granian ASGI (HTTP-only)"
    exec granian \
      --interface asginl \
      --no-ws \
      --host "$host" \
      --port "$port" \
      --workers "${GRANIAN_WORKERS:-2}" \
      config.asgi:application
    ;;
  *)
    echo "Unsupported DJANGO_SERVER='$server'. Expected 'gunicorn' or 'granian'." >&2
    exit 64
    ;;
esac
