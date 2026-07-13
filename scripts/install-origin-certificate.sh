#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source_cert="${1:-}"
source_key="${2:-}"

if [[ -z "$source_cert" || -z "$source_key" ]]; then
  echo "Usage: $0 /path/to/origin.crt /path/to/origin.key" >&2
  exit 2
fi

[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
domain="$(sed -n 's/^APP_DOMAIN=//p' .env | tail -n 1 | tr -d '\r"' | tr -d "'")"
[[ -n "$domain" ]] || { echo "APP_DOMAIN is missing from .env." >&2; exit 1; }

./scripts/check-tls-certificate.sh "$source_cert" "$source_key" "$domain"
mkdir -p secrets/tls
install -m 0644 "$source_cert" secrets/tls/origin.crt
install -m 0600 "$source_key" secrets/tls/origin.key
./scripts/check-tls-certificate.sh secrets/tls/origin.crt secrets/tls/origin.key "$domain"

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
if "${compose[@]}" ps --status running nginx 2>/dev/null | grep -q nginx; then
  "${compose[@]}" exec -T nginx nginx -t
  "${compose[@]}" exec -T nginx nginx -s reload
  echo "Installed the certificate and reloaded Nginx."
else
  echo "Installed the certificate. Nginx is not running yet."
fi
