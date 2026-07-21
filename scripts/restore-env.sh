#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
backup=${1:-.deployment-state/latest-env-backup.env}
[ -f "$backup" ] || { echo "Backup not found: $backup" >&2; exit 1; }
cp .env ".deployment-state/pre-restore-$(date -u +%Y%m%dT%H%M%SZ).env"
cp "$backup" .env
chmod 600 .env 2>/dev/null || true
docker compose config --quiet
docker compose up -d --build web worker beat realtime nginx
echo "Restored environment from $backup"
