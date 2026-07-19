#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env. Copy .env.production.example to .env and fill it first." >&2; exit 1; }

bash ./scripts/update-cloudflare-ips.sh
bash ./scripts/production-readiness.sh --preflight

mkdir -p backups secrets/tls
# The cutover script builds web, frontend, and Rust sequentially, validates the
# schema and Django settings, and only then replaces the running containers.
bash ./scripts/deploy-axum-cutover.sh

bash ./scripts/production-readiness.sh --probe

domain="$(sed -n 's/^APP_DOMAIN=//p' .env | tail -n 1 | tr -d '\r"' | tr -d "'")"
echo "Deployment completed: https://${domain}"
