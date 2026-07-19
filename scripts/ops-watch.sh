#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
log_file="${OPS_LOG_FILE:-logs/operations.log}"
mkdir -p "$(dirname "$log_file")"
set +e
output="$(./scripts/operational-health.sh 2>&1)"
status=$?
set -e
if (( status == 0 )); then
  printf '%s OK %s\n' "$(date -u +%FT%TZ)" "$output" >> "$log_file"
  exit 0
fi
printf '%s FAILED %s\n' "$(date -u +%FT%TZ)" "$output" >> "$log_file"
webhook="${OPS_ALERT_WEBHOOK_URL:-}"
if [[ -n "$webhook" ]]; then
  python3 - "$webhook" "$output" <<'PY'
import json, sys, urllib.request
url, message = sys.argv[1], sys.argv[2]
body=json.dumps({'text': f'Crescentsphere production health check failed:\n{message[:3000]}'}).encode()
req=urllib.request.Request(url, data=body, headers={'content-type':'application/json'}, method='POST')
try:
    urllib.request.urlopen(req, timeout=10).read()
except Exception as exc:
    print(f'Alert webhook failed: {exc}', file=sys.stderr)
PY
fi
exit "$status"
