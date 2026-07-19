#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

requested="${1:-}"
state_file="loadtests/results/.capacity-test-state.json"
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
[[ "$requested" =~ ^[0-9]+$ ]] || { echo "Usage: $0 <temporary-connection-limit>" >&2; exit 2; }
[[ "${CAPACITY_TEST_ACK:-}" == "TEMPORARILY_RAISE_SOCKET_LIMIT" ]] || {
  echo "Set CAPACITY_TEST_ACK=TEMPORARILY_RAISE_SOCKET_LIMIT for this controlled test." >&2
  exit 1
}
[[ ! -f "$state_file" ]] || { echo "A capacity-test state already exists: $state_file" >&2; exit 1; }

max_allowed="$(sed -n 's/^CAPACITY_TEST_MAX_CONNECTIONS=//p' .env | tail -n1 | tr -d '\r\"\047')"
max_allowed="${max_allowed:-1000}"
[[ "$max_allowed" =~ ^[0-9]+$ ]] || { echo "CAPACITY_TEST_MAX_CONNECTIONS must be an integer." >&2; exit 1; }
(( requested >= 10 && requested <= max_allowed )) || {
  echo "Requested temporary limit $requested must be between 10 and $max_allowed." >&2
  exit 1
}

read -r original < <(python3 - <<'PY'
from pathlib import Path
import re
text=Path('.env').read_text()
m=re.search(r'^REALTIME_MAX_CONNECTIONS=([0-9]+)$', text, re.M)
print(m.group(1) if m else 500)
PY
)
(( requested >= original )) || {
  echo "Temporary test limit $requested is below current production limit $original." >&2
  exit 1
}

mkdir -p "$(dirname "$state_file")"
python3 - "$state_file" "$original" "$requested" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
path=Path(sys.argv[1])
path.write_text(json.dumps({
    'original_limit': int(sys.argv[2]),
    'temporary_limit': int(sys.argv[3]),
    'started_at': datetime.now(timezone.utc).isoformat(),
}, indent=2))
path.chmod(0o600)
PY

restore_on_error() {
  local status=$?
  trap - EXIT
  if (( status != 0 )); then
    echo "Capacity-test startup failed; restoring REALTIME_MAX_CONNECTIONS=$original." >&2
    python3 - "$original" <<'PY'
from pathlib import Path
import re, sys
path=Path('.env'); text=path.read_text(); line=f'REALTIME_MAX_CONNECTIONS={sys.argv[1]}'
text=re.sub(r'^REALTIME_MAX_CONNECTIONS=.*$', line, text, flags=re.M) if re.search(r'^REALTIME_MAX_CONNECTIONS=.*$', text, re.M) else text.rstrip()+f'\n{line}\n'
path.write_text(text)
PY
    docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml up -d --no-deps --force-recreate realtime >/dev/null 2>&1 || true
    rm -f "$state_file"
  fi
  exit "$status"
}
trap restore_on_error EXIT

python3 - "$requested" <<'PY'
from pathlib import Path
import re, sys
path=Path('.env'); text=path.read_text(); line=f'REALTIME_MAX_CONNECTIONS={sys.argv[1]}'
text=re.sub(r'^REALTIME_MAX_CONNECTIONS=.*$', line, text, flags=re.M) if re.search(r'^REALTIME_MAX_CONNECTIONS=.*$', text, re.M) else text.rstrip()+f'\n{line}\n'
path.write_text(text)
PY

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
"${compose[@]}" up -d --no-deps --force-recreate realtime
timeout 90 bash -c 'until docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; do sleep 2; done'
trap - EXIT

echo "Temporary Axum connection ceiling raised from $original to $requested."
echo "Run the controlled test, then always execute: ./scripts/end-capacity-test.sh"
