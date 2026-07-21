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
ignored_parts = {
    '.git', '.deployment-state', 'SNM', 'backups', 'node_modules',
    'dist', 'secrets',
}
for path in Path('.').rglob('*.json'):
    if any(part in ignored_parts for part in path.parts):
        continue
    json.loads(path.read_text())
for path in [Path('docker-compose.yml'), Path('docker-compose.local.yml'), Path('docker-compose.production.yml')]:
    yaml.safe_load(path.read_text())
expected_services = {
    'postgres', 'pgbouncer', 'redis', 'nats', 'web',
    'worker', 'beat', 'realtime', 'frontend', 'nginx',
}
compose_services = set(yaml.safe_load(Path('docker-compose.yml').read_text())['services'])
missing = expected_services - compose_services
if missing:
    raise SystemExit(f'Compose is missing required production services: {sorted(missing)}')
for path in [
    Path('scripts/deploy-axum-cutover.sh'),
    Path('scripts/production-readiness.sh'),
    Path('scripts/rollback-release.sh'),
]:
    script = path.read_text()
    missing = {service for service in expected_services if service not in script}
    if missing:
        raise SystemExit(f'{path} is missing production services: {sorted(missing)}')
nats_template = Path('deploy/nats/nats.conf').read_text()
for placeholder in (
    '__NATS_APP_USER__', '__NATS_APP_PASSWORD__',
    '__NATS_REALTIME_USER__', '__NATS_REALTIME_PASSWORD__',
):
    if f'"{placeholder}"' not in nats_template:
        raise SystemExit(f'NATS template is missing quoted placeholder: {placeholder}')
if '$NATS_APP_PASSWORD' in nats_template or '$NATS_REALTIME_PASSWORD' in nats_template:
    raise SystemExit('NATS passwords must be rendered as quoted strings, not parsed as raw variables')
compose_text = Path('docker-compose.yml').read_text()
for required in ('/etc/nats/render-config.sh', '/etc/nats/nats.conf.template'):
    if required not in compose_text:
        raise SystemExit(f'Compose is missing the safe NATS renderer mount: {required}')
nats_renderer = Path('deploy/nats/render-config.sh').read_text()
if 'command -v nats-server' not in nats_renderer or 'exec /nats-server' in nats_renderer:
    raise SystemExit('NATS renderer must resolve nats-server from the image PATH')
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
