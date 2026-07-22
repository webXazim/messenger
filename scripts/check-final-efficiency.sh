#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
fail() { echo "ERROR: $*" >&2; exit 1; }
require() { grep -Fq "$2" "$1" || fail "$3"; }
reject_file() { [ ! -e "$1" ] || fail "$2"; }

require realtime/src/admission.rs 'code": "realtime_overloaded' 'Axum overload shedding response is missing'
require realtime/src/main.rs 'middleware::from_fn_with_state' 'Axum admission middleware is not installed'
require realtime/src/main.rs 'realtime_http_rejected_read_total' 'HTTP overload metrics are missing'
require realtime/src/main.rs 'realtime_sqlx_pool_connections' 'SQLx pool metrics are missing'
require realtime/src/main.rs 'realtime_websocket_high_queue_messages' 'WebSocket queue metrics are missing'
require realtime/src/database.rs '.max_connections(config.sqlx_max_connections)' 'SQLx maximum pool limit is missing'
require realtime/src/database.rs '.idle_timeout(Some(config.sqlx_idle_timeout))' 'SQLx idle timeout is missing'
require realtime/src/registry.rs 'pub fn queue_snapshot' 'WebSocket queue-pressure snapshot is missing'
require config/settings.py 'AXUM_DATA_PLANE_REQUIRED' 'Strict final data-plane gate is missing'
require config/settings.py 'DATABASE_MAINTENANCE_MODE' 'Explicit database maintenance mode is missing'
require entrypoint.sh 'DATABASE_MAINTENANCE_MODE=True' 'Startup migrations do not use explicit database maintenance mode'
require apps/chat/checks.py 'chat.E030' 'Django deployment check for final Axum plane is missing'
require scripts/stack-profile.sh 'final|efficient)' 'Final efficient rollout profile is missing'
require scripts/stack-profile.sh './scripts/generate-realtime-lockfile.sh' 'Final rollout does not prepare the Axum lockfile'
require scripts/operational-health.sh 'axum_pressure=' 'Operational pressure validation is missing'
require scripts/deploy-axum-cutover.sh 'media-worker' 'Production cutover does not deploy the Rust media worker'
require scripts/deploy-axum-cutover.sh './scripts/generate-media-worker-lockfile.sh' 'Production cutover does not prepare the media-worker lockfile'
require scripts/production-readiness.sh 'required_services+=(media-worker)' 'Production readiness does not require the Rust media worker'
require loadtests/k6/overload-protection.js 'realtime_overloaded' 'Overload load test is missing'
require scripts/run-load-test.sh 'overload)' 'Overload test runner mode is missing'
require scripts/run-load-test.sh 'soak)' 'Soak test runner mode is missing'
require nginx/snm.production.conf 'limit_conn realtime_per_ip 50;' 'WebSocket connection protection is missing'
require nginx/snm.production.conf 'proxy_buffering on;' 'Fast API response buffering is missing'

if grep -Eq 'AXUM_DATA_PLANE_REQUIRED=False' \
  entrypoint.sh scripts/deploy-axum-cutover.sh scripts/production-readiness.sh \
  scripts/verify-axum-runtime.sh scripts/verify-axum-feature-parity.sh; then
  fail 'Production maintenance paths must not disable the strict Axum data-plane gate'
fi

for obsolete in \
  realtime/src/redis_stream.rs \
  apps/chat/consumers.py apps/chat/websocket_routing.py \
  apps/support/consumers.py apps/support/websocket_routing.py apps/support/tests_realtime.py; do
  reject_file "$obsolete" "Obsolete realtime file remains: $obsolete"
done

bash ./scripts/check-axum-hot-paths.sh
bash ./scripts/check-axum-support-data.sh
bash ./scripts/check-axum-direct-outbox.sh
bash ./scripts/check-axum-call-runtime.sh
bash ./scripts/check-rust-media-worker.sh

python3 -m py_compile config/settings.py apps/chat/checks.py scripts/analyze-load-test.py scripts/capture_load_test_metrics.py
python3 - <<'PY'
import yaml
from pathlib import Path
for name in ('docker-compose.yml', 'docker-compose.local.yml', 'docker-compose.production.yml'):
    with Path(name).open(encoding='utf-8') as handle:
        yaml.safe_load(handle)
prod=Path('.env.production.example').read_text(encoding='utf-8')
required={
    'AXUM_DATA_PLANE_REQUIRED':'True', 'DATABASE_MAINTENANCE_MODE':'False',
    'CHAT_READ_BACKEND':'sqlx',
    'CHAT_COMMAND_BACKEND':'axum', 'CHAT_INTERACTION_BACKEND':'axum',
    'CHAT_MESSAGE_MUTATION_BACKEND':'axum', 'CHAT_CALL_RUNTIME_BACKEND':'axum',
    'CHAT_ATTACHMENT_BACKEND':'axum', 'CHAT_CONVERSATION_COMMAND_BACKEND':'axum',
    'SUPPORT_DATA_BACKEND':'axum', 'MEDIA_PROCESSING_BACKEND':'rust',
    'MEDIA_WORKER_DJANGO_FALLBACK_ENABLED':'False', 'DATABASE_RUNTIME_ENDPOINT':'pgbouncer',
    'REALTIME_PRESENCE_BACKEND':'local', 'REALTIME_OUTBOX_PUBLISHER':'axum',
}
settings={}
for line in prod.splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key,value=line.split('=',1); settings[key]=value
bad=[f'{k}={settings.get(k)!r}' for k,v in required.items() if settings.get(k)!=v]
if bad: raise SystemExit('Final production example is incomplete: '+', '.join(bad))
print('Final production selectors verified.')
PY

for script in scripts/*.sh; do
  case "$(head -n1 "$script")" in
    *bash*) bash -n "$script" ;;
    *) sh -n "$script" ;;
  esac
done
if command -v node >/dev/null 2>&1; then
  for file in loadtests/k6/*.js loadtests/k6/lib/*.js; do node --check "$file"; done
else
  echo 'Node.js is not installed; skipping optional load-test JavaScript syntax checks.'
fi

echo 'Final efficiency hardening source contracts passed.'
