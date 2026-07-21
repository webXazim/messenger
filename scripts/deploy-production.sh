#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env. Copy .env.production.example to .env and fill it first." >&2; exit 1; }

bash ./scripts/update-cloudflare-ips.sh
bash ./scripts/production-readiness.sh --preflight

mkdir -p backups secrets/tls
# The cutover script builds every application image sequentially, validates
# infrastructure, schema, Django, NATS, and workers, and only then completes.
bash ./scripts/deploy-axum-cutover.sh

bash ./scripts/production-readiness.sh --probe

domain="$(sed -n 's/^APP_DOMAIN=//p' .env | tail -n 1 | tr -d '\r"' | tr -d "'")"
echo "Deployment completed: https://${domain}"
