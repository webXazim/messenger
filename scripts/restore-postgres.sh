#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

backup="${1:-}"
confirmation="${2:-}"
[[ -n "$backup" ]] || { echo "Usage: $0 backups/file.dump --confirm" >&2; exit 2; }
[[ "$confirmation" == "--confirm" ]] || { echo "Restore is destructive. Re-run with --confirm." >&2; exit 2; }
[[ -f "$backup" ]] || { echo "Backup not found: $backup" >&2; exit 1; }
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }

case "$backup" in
  *.dump) backup_type="dump" ;;
  *.sql.gz) backup_type="sql-gzip" ;;
  *.sql) backup_type="sql" ;;
  *) echo "Unsupported backup format. Use .dump, .sql.gz, or .sql." >&2; exit 1 ;;
esac

read_env() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r"' | tr -d "'"
}
user="$(read_env DB_USER)"
database="$(read_env DB_NAME)"
[[ "$user" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "DB_USER is not a safe PostgreSQL identifier." >&2; exit 1; }
[[ "$database" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "DB_NAME is not a safe PostgreSQL identifier." >&2; exit 1; }

if [[ -f "${backup}.sha256" ]]; then
  sha256sum -c "${backup}.sha256"
fi

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
case "$backup_type" in
  dump) "${compose[@]}" exec -T postgres pg_restore --list < "$backup" >/dev/null ;;
  sql-gzip) gzip -t "$backup" ;;
  sql) [[ -s "$backup" ]] || { echo "SQL backup is empty." >&2; exit 1; } ;;
esac

restart_services=0
cleanup() {
  if (( restart_services )); then
    "${compose[@]}" start web worker beat nginx >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Create a safety backup immediately before replacing the database.
./scripts/backup-postgres.sh

"${compose[@]}" stop web worker beat nginx
restart_services=1
"${compose[@]}" exec -T postgres psql -U "$user" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${database}' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"${database}\";" \
  -c "CREATE DATABASE \"${database}\" OWNER \"${user}\";"

case "$backup_type" in
  dump)
    "${compose[@]}" exec -T postgres pg_restore -U "$user" -d "$database" \
      --no-owner --no-acl --exit-on-error < "$backup"
    ;;
  sql-gzip)
    gzip -dc "$backup" | "${compose[@]}" exec -T postgres psql -U "$user" -d "$database" -v ON_ERROR_STOP=1
    ;;
  sql)
    "${compose[@]}" exec -T postgres psql -U "$user" -d "$database" -v ON_ERROR_STOP=1 < "$backup"
    ;;
esac

"${compose[@]}" start web worker beat nginx
restart_services=0
"${compose[@]}" exec -T web python manage.py migrate --noinput
"${compose[@]}" exec -T web python manage.py check
"${compose[@]}" exec -T web python manage.py check_chat_readiness

echo "Database restore completed from $backup"
