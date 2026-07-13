#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
mkdir -p backups

read_env() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r"' | tr -d "'"
}

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="backups/messenger-${stamp}.dump"
tmp="${output}.partial"
user="$(read_env DB_USER)"
database="$(read_env DB_NAME)"
[[ "$user" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "DB_USER is not a safe PostgreSQL identifier." >&2; exit 1; }
[[ "$database" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "DB_NAME is not a safe PostgreSQL identifier." >&2; exit 1; }

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
trap 'rm -f "$tmp"' EXIT
"${compose[@]}" exec -T postgres pg_dump \
  -U "$user" -d "$database" --format=custom --compress=9 --no-owner --no-acl > "$tmp"

[[ -s "$tmp" ]] || { echo "Backup file is empty." >&2; exit 1; }
"${compose[@]}" exec -T postgres pg_restore --list < "$tmp" >/dev/null
mv "$tmp" "$output"
trap - EXIT
sha256sum "$output" > "${output}.sha256"
chmod 0600 "$output" "${output}.sha256"
echo "Created and verified $output"
echo "Copy the dump and checksum to encrypted off-server storage."
