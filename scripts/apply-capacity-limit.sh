#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

report="${1:-loadtests/results/capacity-report.json}"
requested="${2:-}"
[[ -f "$report" ]] || { echo "Missing capacity report: $report" >&2; exit 1; }
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }

current_fingerprint="$(python3 scripts/deployment_fingerprint.py --hash)"
read -r passed complete recommended report_fingerprint fresh < <(python3 - "$report" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
valid_until=r.get('valid_until')
fresh=False
if valid_until:
    expiry=datetime.fromisoformat(valid_until.replace('Z', '+00:00'))
    if expiry.tzinfo is None:
        expiry=expiry.replace(tzinfo=timezone.utc)
    fresh=datetime.now(timezone.utc) < expiry
print(
    str(bool(r.get('passed'))).lower(),
    str(bool(r.get('verification_complete'))).lower(),
    int(r.get('recommended_production_max_connections') or 0),
    str(r.get('deployment_fingerprint') or ''),
    str(fresh).lower(),
)
PY
)

[[ "$passed" == true && "$complete" == true && "$recommended" -gt 0 ]] || {
  echo "The report did not pass complete verification; capacity will not be changed." >&2
  exit 1
}
[[ "$fresh" == true ]] || { echo "The capacity report has expired; rerun the suite." >&2; exit 1; }
[[ -n "$report_fingerprint" && "$report_fingerprint" == "$current_fingerprint" ]] || {
  echo "The current deployment differs from the deployment that was load-tested." >&2
  echo "Report:  $report_fingerprint" >&2
  echo "Current: $current_fingerprint" >&2
  exit 1
}

value="${requested:-$recommended}"
[[ "$value" =~ ^[0-9]+$ ]] || { echo "Capacity must be an integer." >&2; exit 1; }
(( value > 0 && value <= recommended )) || {
  echo "Requested value $value exceeds verified recommendation $recommended." >&2
  exit 1
}
[[ "${CAPACITY_CHANGE_ACK:-}" == "APPLY_VERIFIED_CAPACITY" ]] || {
  echo "Set CAPACITY_CHANGE_ACK=APPLY_VERIFIED_CAPACITY to update .env." >&2
  exit 1
}

python3 - "$value" <<'PY'
from pathlib import Path
import re, sys
path=Path('.env')
text=path.read_text()
line=f"REALTIME_MAX_CONNECTIONS={sys.argv[1]}"
if re.search(r'^REALTIME_MAX_CONNECTIONS=.*$', text, flags=re.M):
    text=re.sub(r'^REALTIME_MAX_CONNECTIONS=.*$', line, text, flags=re.M)
else:
    text += ('\n' if text and not text.endswith('\n') else '') + line + '\n'
path.write_text(text)
PY

echo "REALTIME_MAX_CONNECTIONS updated to $value. Restart realtime during a controlled deployment window."
echo "Any later image or performance-setting change invalidates this report and requires a new capacity suite."
