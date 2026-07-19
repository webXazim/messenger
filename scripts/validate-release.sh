#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python_bin="${PYTHON_BIN:-}"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 || command -v python || true)"
fi
[[ -n "$python_bin" ]] || { echo "python3 is required." >&2; exit 1; }

"$python_bin" -m compileall -q config apps scripts
"$python_bin" - <<'PY'
import json
from pathlib import Path
import yaml
for path in Path('.').rglob('*.json'):
    if any(part in {'node_modules', 'dist'} for part in path.parts):
        continue
    json.loads(path.read_text())
for path in [Path('docker-compose.yml'), Path('docker-compose.local.yml'), Path('docker-compose.production.yml')]:
    yaml.safe_load(path.read_text())
print('Python, JSON, and YAML validation passed.')
PY

for script in scripts/*.sh entrypoint.sh snm-dev.sh; do
  bash -n "$script"
done

"$python_bin" scripts/test-capacity-analyzer.py

if command -v node >/dev/null; then
  for script in loadtests/k6/*.js loadtests/k6/lib/*.js; do
    node --check "$script"
  done
fi

if find loadtests/data -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  echo "Temporary load-test credential JSON must be removed before release validation." >&2
  exit 1
fi

(
  cd frontend
  if [[ ! -x node_modules/.bin/vite || ! -f node_modules/typescript/bin/tsc ]]; then
    rm -rf node_modules
    npm ci
  fi
  npm run check:product
)

if [[ "${1:-}" == "--with-docker" ]]; then
  ./scripts/test-backend-docker.sh
  docker compose -f docker-compose.yml -f docker-compose.production.yml config >/dev/null
fi

echo "Release validation passed."
