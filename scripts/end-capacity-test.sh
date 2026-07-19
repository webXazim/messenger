#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
state_file="loadtests/results/.capacity-test-state.json"
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
[[ -f "$state_file" ]] || { echo "No active capacity-test state exists." >&2; exit 1; }
original="$(python3 - "$state_file" <<'PY'
import json,sys
print(int(json.load(open(sys.argv[1]))['original_limit']))
PY
)"
python3 - "$original" <<'PY'
from pathlib import Path
import re, sys
path=Path('.env'); text=path.read_text(); line=f'REALTIME_MAX_CONNECTIONS={sys.argv[1]}'
text=re.sub(r'^REALTIME_MAX_CONNECTIONS=.*$', line, text, flags=re.M) if re.search(r'^REALTIME_MAX_CONNECTIONS=.*$', text, re.M) else text.rstrip()+f'\n{line}\n'
path.write_text(text)
PY
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
"${compose[@]}" up -d --no-deps --force-recreate realtime
timeout 90 bash -c 'until docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready >/dev/null 2>&1; do sleep 2; done'
rm -f "$state_file"
echo "Production Axum connection ceiling restored to $original."
