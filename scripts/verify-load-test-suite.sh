#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

suite="${1:-loadtests/results/final-suite}"
[[ -d "$suite" ]] || { echo "Missing suite directory: $suite" >&2; exit 1; }

for mode in smoke capacity api reconnect mixed; do
  marker="$suite/passed-${mode}.json"
  [[ -f "$marker" ]] || { echo "Missing successful $mode marker in $suite" >&2; exit 1; }
  python3 - "$marker" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
p=json.loads(Path(sys.argv[1]).read_text())
if p.get('passed') is not True:
    raise SystemExit(f"Failed marker: {sys.argv[1]}")
completed=p.get('completed_at')
if not completed:
    raise SystemExit(f"Marker has no completed_at: {sys.argv[1]}")
when=datetime.fromisoformat(completed.replace('Z', '+00:00'))
if when.tzinfo is None:
    when=when.replace(tzinfo=timezone.utc)
if (datetime.now(timezone.utc)-when).total_seconds() > 7*24*3600:
    raise SystemExit(f"Marker is older than seven days: {sys.argv[1]}")
PY
done

for artifact in capacity-report.json postgres-audit.json critical-query-plans.json; do
  [[ -f "$suite/$artifact" ]] || { echo "Missing $artifact in $suite" >&2; exit 1; }
done

python3 - "$suite/capacity-report.json" "$suite/postgres-audit.json" "$suite/critical-query-plans.json" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path

report=json.loads(Path(sys.argv[1]).read_text())
audit=json.loads(Path(sys.argv[2]).read_text())
plans=json.loads(Path(sys.argv[3]).read_text())
if report.get('schema_version') != 3:
    raise SystemExit('Capacity report schema is not the final schema version.')
if report.get('passed') is not True or report.get('verification_complete') is not True:
    raise SystemExit('Capacity report did not pass complete verification.')
if int(report.get('recommended_production_max_connections') or 0) <= 0:
    raise SystemExit('Capacity report has no safe production recommendation.')
valid_until=report.get('valid_until')
if not valid_until:
    raise SystemExit('Capacity report has no expiration.')
expiry=datetime.fromisoformat(valid_until.replace('Z', '+00:00'))
if expiry.tzinfo is None:
    expiry=expiry.replace(tzinfo=timezone.utc)
if datetime.now(timezone.utc) >= expiry:
    raise SystemExit('Capacity report has expired; rerun the suite against the current release.')
if not report.get('deployment_fingerprint'):
    raise SystemExit('Capacity report is not bound to a deployment fingerprint.')
if audit.get('passed') is not True:
    raise SystemExit('PostgreSQL performance audit did not pass.')
if plans.get('strict_passed') is not True or plans.get('analyzed') is not True:
    raise SystemExit('Critical query plans were not fully analyzed or have unresolved warnings.')
if plans.get('skipped'):
    raise SystemExit('Critical query plan coverage is incomplete.')
print(
    'Verified final load-test suite. '
    f"Recommended production max: {report['recommended_production_max_connections']}"
)
PY
