#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-smoke}"
target="${2:-}"
data_file="${LOADTEST_DATA_FILE:-loadtests/data/users.json}"
[[ -f "$data_file" ]] || { echo "Missing credential file: $data_file" >&2; exit 1; }
[[ -n "${LOADTEST_BASE_URL:-}" ]] || { echo "Set LOADTEST_BASE_URL." >&2; exit 1; }
[[ -n "${LOADTEST_WS_URL:-}" ]] || { echo "Set LOADTEST_WS_URL." >&2; exit 1; }
[[ -n "${LOADTEST_ORIGIN:-}" ]] || { echo "Set LOADTEST_ORIGIN." >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is required on the external load generator." >&2; exit 1; }

image="${K6_IMAGE:-grafana/k6:latest}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
result_dir="${LOADTEST_RESULT_DIR:-loadtests/results/${timestamp}-${mode}}"
mkdir -p "$result_dir"
result_dir="$(cd "$result_dir" && pwd)"
data_dir="$(cd "$(dirname "$data_file")" && pwd)"
data_name="$(basename "$data_file")"

user_count="$(python3 - "$data_file" <<'PY_USERS'
import json, sys
print(int(json.load(open(sys.argv[1])).get('user_count') or 0))
PY_USERS
)"
(( user_count >= 2 )) || { echo "Credential file contains no usable users." >&2; exit 1; }

common=(
  --rm
  --user "$(id -u):$(id -g)"
  -e "LOADTEST_BASE_URL=$LOADTEST_BASE_URL"
  -e "LOADTEST_WS_URL=$LOADTEST_WS_URL"
  -e "LOADTEST_ORIGIN=$LOADTEST_ORIGIN"
  -e "LOADTEST_DATA=/data/$data_name"
  -v "$PWD/loadtests:/scripts:ro"
  -v "$data_dir:/data:ro"
  -v "$result_dir:/results"
  "$image"
  run
)

case "$mode" in
  smoke)
    target="${target:-10}"
    script="/scripts/k6/realtime-capacity.js"
    envs=(-e "TARGET_VUS=$target" -e RAMP_DURATION=10s -e HOLD_DURATION=30s -e RAMP_DOWN_DURATION=10s -e SOCKET_LIFETIME_MS=45000)
    summary="/results/realtime-smoke.json"
    ;;
  capacity)
    target="${target:-500}"
    script="/scripts/k6/realtime-capacity.js"
    envs=(-e "TARGET_VUS=$target" -e "RAMP_DURATION=${RAMP_DURATION:-90s}" -e "HOLD_DURATION=${HOLD_DURATION:-300s}" -e "RAMP_DOWN_DURATION=${RAMP_DOWN_DURATION:-45s}" -e "SOCKET_LIFETIME_MS=${SOCKET_LIFETIME_MS:-420000}")
    summary="/results/realtime-capacity-${target}.json"
    ;;
  reconnect)
    target="${target:-250}"
    script="/scripts/k6/reconnect-storm.js"
    envs=(-e "TARGET_VUS=$target" -e "ITERATIONS_PER_VU=${ITERATIONS_PER_VU:-3}" -e "RECONNECT_HOLD_MS=${RECONNECT_HOLD_MS:-3000}")
    summary="/results/reconnect-${target}.json"
    ;;
  api)
    target="${target:-10}"
    script="/scripts/k6/api-message-flow.js"
    envs=(-e "MESSAGE_RATE=$target" -e "MESSAGE_DURATION=${MESSAGE_DURATION:-180s}" -e "PREALLOCATED_VUS=${PREALLOCATED_VUS:-50}" -e "MAX_VUS=${MAX_VUS:-150}")
    summary="/results/api-message-${target}rps.json"
    ;;
  mixed)
    target="${target:-250}"
    script="/scripts/k6/mixed-production.js"
    envs=(
      -e "MIXED_SOCKET_VUS=$target"
      -e "MIXED_WARMUP_DURATION=${MIXED_WARMUP_DURATION:-60s}"
      -e "MIXED_HOLD_DURATION=${MIXED_HOLD_DURATION:-300s}"
      -e "MIXED_RAMP_DOWN_DURATION=${MIXED_RAMP_DOWN_DURATION:-30s}"
      -e "MIXED_SOCKET_LIFETIME_MS=${MIXED_SOCKET_LIFETIME_MS:-390000}"
      -e "MIXED_READ_RATE=${MIXED_READ_RATE:-20}"
      -e "MIXED_WRITE_RATE=${MIXED_WRITE_RATE:-5}"
      -e "MIXED_READ_PREALLOCATED_VUS=${MIXED_READ_PREALLOCATED_VUS:-40}"
      -e "MIXED_READ_MAX_VUS=${MIXED_READ_MAX_VUS:-120}"
      -e "MIXED_WRITE_PREALLOCATED_VUS=${MIXED_WRITE_PREALLOCATED_VUS:-25}"
      -e "MIXED_WRITE_MAX_VUS=${MIXED_WRITE_MAX_VUS:-80}"
    )
    summary="/results/mixed-production-${target}.json"
    ;;
  *)
    echo "Usage: $0 {smoke|capacity|reconnect|api|mixed} [target]" >&2
    exit 2
    ;;
esac


if [[ "$mode" == "smoke" || "$mode" == "capacity" || "$mode" == "reconnect" || "$mode" == "mixed" ]]; then
  (( target <= user_count )) || { echo "Target $target exceeds credential user count $user_count." >&2; exit 1; }
elif [[ "$mode" == "api" ]]; then
  api_max_vus="${MAX_VUS:-150}"
  (( api_max_vus <= user_count )) || { echo "MAX_VUS=$api_max_vus exceeds credential user count $user_count." >&2; exit 1; }
fi

if [[ "$mode" == "mixed" ]]; then
  mixed_read_max="${MIXED_READ_MAX_VUS:-120}"
  mixed_write_max="${MIXED_WRITE_MAX_VUS:-80}"
  (( mixed_read_max <= user_count )) || { echo "MIXED_READ_MAX_VUS=$mixed_read_max exceeds credential user count $user_count." >&2; exit 1; }
  (( mixed_write_max <= user_count )) || { echo "MIXED_WRITE_MAX_VUS=$mixed_write_max exceeds credential user count $user_count." >&2; exit 1; }
fi

echo "Starting $mode test. Results: $result_dir"
docker run "${envs[@]}" "${common[@]}" --summary-export "$summary" "$script"
python3 - "$result_dir/passed-${mode}.json" "$mode" "$target" <<'PY_MARKER'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "passed": True,
    "mode": sys.argv[2],
    "target": int(sys.argv[3]),
    "completed_at": datetime.now(timezone.utc).isoformat(),
}, indent=2))
PY_MARKER
echo "k6 test completed: $result_dir"
