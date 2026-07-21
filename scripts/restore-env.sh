#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
backup=${1:-.deployment-state/latest-env-backup.env}
[ -f "$backup" ] || { echo "Backup not found: $backup" >&2; exit 1; }
cp .env ".deployment-state/pre-restore-$(date -u +%Y%m%dT%H%M%SZ).env"
cp "$backup" .env
chmod 600 .env 2>/dev/null || true
compose="docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml"
$compose config --quiet
for service in pgbouncer web worker beat realtime frontend; do
  COMPOSE_PARALLEL_LIMIT=1 $compose build "$service"
done
$compose up -d --remove-orphans postgres pgbouncer redis nats web worker beat realtime frontend nginx
$compose up -d --force-recreate --no-deps nginx
bash ./scripts/production-readiness.sh --probe
echo "Restored environment from $backup"
