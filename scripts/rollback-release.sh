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
./scripts/backup-production.sh
cd "$previous"
./scripts/production-readiness.sh --preflight
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build web realtime frontend
"${compose[@]}" up -d --remove-orphans postgres redis web worker beat realtime frontend nginx
./scripts/production-readiness.sh --probe
echo "Application rollback completed from $previous"
