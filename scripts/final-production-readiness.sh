#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }

report="${1:-loadtests/results/capacity-report.json}"
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)

./scripts/production-readiness.sh --probe
./scripts/operational-health.sh
"${compose[@]}" exec -T web python manage.py emit_realtime_canary --timeout 30
"${compose[@]}" exec -T web python manage.py audit_postgres_performance

require_report="$(sed -n 's/^REQUIRE_VERIFIED_CAPACITY_REPORT=//p' .env | tail -n1 | tr '[:upper:]' '[:lower:]' | tr -d '\r"' | tr -d "'")"
case "$require_report" in
  1|true|yes|on) require_report=true ;;
  *) require_report=false ;;
esac

if [[ -f "$report" ]]; then
  current_fingerprint="$(python3 scripts/deployment_fingerprint.py --hash)"
  python3 - "$report" .env "$current_fingerprint" <<'PY'
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
report=json.loads(Path(sys.argv[1]).read_text())
if report.get('schema_version') != 2:
    raise SystemExit('Capacity report uses an obsolete schema.')
if report.get('passed') is not True or report.get('verification_complete') is not True:
    raise SystemExit('Capacity report did not pass complete verification.')
valid_until=report.get('valid_until')
if not valid_until:
    raise SystemExit('Capacity report has no validity period.')
expiry=datetime.fromisoformat(valid_until.replace('Z', '+00:00'))
if expiry.tzinfo is None:
    expiry=expiry.replace(tzinfo=timezone.utc)
if datetime.now(timezone.utc) >= expiry:
    raise SystemExit('Capacity report has expired.')
recommended=int(report.get('recommended_production_max_connections') or 0)
env=Path(sys.argv[2]).read_text()
match=re.search(r'^REALTIME_MAX_CONNECTIONS=([0-9]+)$', env, re.M)
configured=int(match.group(1)) if match else 500
if not recommended or configured > recommended:
    raise SystemExit(
        f'Configured REALTIME_MAX_CONNECTIONS={configured} exceeds verified recommendation={recommended}.'
    )
report_fingerprint=str(report.get('deployment_fingerprint') or '')
current_fingerprint=sys.argv[3]
if not report_fingerprint or report_fingerprint != current_fingerprint:
    raise SystemExit('The deployed images or performance settings differ from the tested deployment.')
print(
    f'Capacity report passed: configured={configured} '
    f'verified_recommendation={recommended} valid_until={expiry.isoformat()}'
)
PY
else
  if [[ "$require_report" == true ]]; then
    echo "A verified capacity report is required but was not found at $report." >&2
    exit 1
  fi
  echo "WARNING: no capacity report found at $report; correctness passed but capacity remains unverified." >&2
fi

if find loadtests/data -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  echo "Temporary load-test credential files still exist under loadtests/data." >&2
  exit 1
fi
if grep -R -n -E 'channels_redis|daphne|ProtocolTypeRouter|WebsocketCommunicator' requirements.txt config apps realtime docker-compose*.yml 2>/dev/null; then
  echo "Legacy Channels runtime references remain in active source/configuration." >&2
  exit 1
fi

echo "Final production readiness passed. Axum is the sole realtime runtime and measured limits are deployment-bound."
