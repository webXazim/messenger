#!/usr/bin/env bash
set -euo pipefail
current="$(cd "$(dirname "$0")/.." && pwd)"
previous="${1:-}"
confirmation="${2:-}"
[[ -d "$previous" ]] || { echo "Usage: $0 /absolute/path/to/previous-release --confirm" >&2; exit 2; }
previous="$(cd "$previous" && pwd)"
[[ "$confirmation" == "--confirm" ]] || { echo "Application rollback requires --confirm." >&2; exit 2; }
[[ -f "$previous/docker-compose.yml" && -f "$previous/docker-compose.production.yml" ]] || { echo "Previous release is incomplete." >&2; exit 1; }
[[ -f "$current/.env" ]] || { echo "Current .env is missing." >&2; exit 1; }

echo "This rolls application containers back but does not reverse database migrations."
cp -f "$current/.env" "$previous/.env"
if [[ -L "$previous/secrets" ]]; then
  rm "$previous/secrets"
fi
mkdir -p "$previous/secrets"
cp -a "$current/secrets/." "$previous/secrets/"
cd "$current"
bash ./scripts/backup-production.sh
cd "$previous"
bash ./scripts/production-readiness.sh --preflight
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
for service in pgbouncer web worker beat realtime media-worker frontend; do
  COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build "$service"
done
"${compose[@]}" --profile rust-media up -d --remove-orphans postgres pgbouncer redis nats web worker beat realtime media-worker frontend nginx
"${compose[@]}" up -d --force-recreate --no-deps nginx
bash ./scripts/production-readiness.sh --probe
echo "Application rollback completed from $previous"
