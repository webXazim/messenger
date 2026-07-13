#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
app="${1:-}"
target="${2:-}"
confirmation="${3:-}"

if [[ -z "$app" || -z "$target" || "$confirmation" != "--confirm" ]]; then
  echo "Usage: $0 <app_label> <target_migration_or_zero> --confirm" >&2
  exit 2
fi

./scripts/backup-postgres.sh
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
"${compose[@]}" exec -T web python manage.py showmigrations "$app"
"${compose[@]}" exec -T web python manage.py migrate "$app" "$target" --noinput
"${compose[@]}" exec -T web python manage.py check
"${compose[@]}" exec -T web python manage.py showmigrations "$app"

echo "Migration rollback completed for $app to $target."
echo "Forward migrations will remain pending until the next deployment or an explicit migrate command."
