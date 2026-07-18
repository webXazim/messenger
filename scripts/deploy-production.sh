#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env. Copy .env.production.example to .env and fill it first." >&2; exit 1; }

python_bin="${PYTHON_BIN:-}"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 || command -v python || true)"
fi
[[ -n "$python_bin" ]] || { echo "python3 is required by the deployment status parser." >&2; exit 1; }

bash ./scripts/update-cloudflare-ips.sh
bash ./scripts/production-readiness.sh --preflight

mkdir -p backups secrets/tls
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
"${compose[@]}" up -d --build --remove-orphans

# Wait for the application services before running deep checks.
deadline=$((SECONDS + 180))
unhealthy="starting"
while (( SECONDS < deadline )); do
  if ! ps_json="$("${compose[@]}" ps --format json 2>/dev/null)"; then
    unhealthy="compose-ps-failed"
  else
    unhealthy="$(PS_JSON="$ps_json" "$python_bin" - <<'PYJSON'
import json
import os

text = os.environ.get("PS_JSON", "").strip()
required = {"web", "frontend", "nginx", "postgres", "redis", "worker", "beat", "turn"}
if not text:
    print("no-services")
    raise SystemExit
try:
    try:
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = [rows]
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
except Exception:
    print("status-parse-error")
    raise SystemExit

bad = []
seen = set()
for row in rows:
    service = row.get("Service") or row.get("service") or ""
    if service not in required:
        continue
    seen.add(service)
    state = str(row.get("State") or row.get("state") or "").lower()
    health = str(row.get("Health") or row.get("health") or "").lower()
    if state != "running" or (health and health != "healthy"):
        bad.append(f"{service}:{state or 'unknown'}/{health or 'no-health'}")
for missing in sorted(required - seen):
    bad.append(f"{missing}:missing")
print(",".join(bad))
PYJSON
)"
  fi
  [[ -z "$unhealthy" ]] && break
  sleep 5
done

if [[ -n "$unhealthy" ]]; then
  "${compose[@]}" ps
  echo "Services did not become ready: $unhealthy" >&2
  exit 1
fi

bash ./scripts/production-readiness.sh --probe

domain="$(sed -n 's/^APP_DOMAIN=//p' .env | tail -n 1 | tr -d '\r"' | tr -d "'")"
echo "Deployment completed: https://${domain}"
