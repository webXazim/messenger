#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

require() {
  pattern=$1
  file=$2
  grep -Fq "$pattern" "$file" || {
    echo "Missing call-runtime contract: $pattern in $file" >&2
    exit 1
  }
}

require 'mod call_runtime;' realtime/src/main.rs
require 'mod call_signal_store;' realtime/src/main.rs
require 'CHAT_CALL_RUNTIME_BACKEND' realtime/src/config.rs
require 'chat_call_runtime_backend' realtime/src/main.rs
require '/calls/{call_id}/signal/' realtime/src/main.rs
require '/calls/{call_id}/heartbeat/' realtime/src/main.rs
require '/calls/{call_id}/media-state/' realtime/src/main.rs
require '/calls/{call_id}/quality-report/' realtime/src/main.rs
require '/calls/{call_id}/speaker-state/' realtime/src/main.rs
require '/calls/{call_id}/orchestration/' realtime/src/main.rs
require '/calls/{call_id}/diagnostics/' realtime/src/main.rs
require 'FOR UPDATE OF p,cp' realtime/src/call_runtime.rs
require 'cp.left_at IS NULL AND cp.is_blocked=FALSE' realtime/src/call_runtime.rs
require 'recommendation' realtime/src/call_runtime.rs
require 'Arc::strong_count(queue) == 1' realtime/src/call_signal_store.rs
require 'if let Some(response) = require_axum(&state)' realtime/src/call_commands.rs
require 'publish_shared_after_local' realtime/src/call_runtime.rs
require 'CallSignalStore' realtime/src/call_signal_store.rs
require 'REALTIME_EPHEMERAL_BACKEND=nats' scripts/stack-profile.sh
require 'calls-shadow' scripts/stack-profile.sh
require 'calls)' scripts/stack-profile.sh
require 'CHAT_CALL_RUNTIME_BACKEND=axum' scripts/stack-profile.sh
require 'VITE_CHAT_CALL_RUNTIME_BACKEND' frontend/src/lib/config.ts
require 'chat_call_runtime_backend' scripts/verify-axum-runtime.sh
require 'expected_call_runtime_backend' scripts/deploy-axum-cutover.sh

# Runtime state and HTTP signaling must stay out of the durable outbox path.
if grep -Eq 'common_realtimeoutboxevent|INSERT INTO common_realtimeoutboxevent|jetstream' realtime/src/call_runtime.rs; then
  echo 'Call runtime accidentally uses durable outbox/JetStream.' >&2
  exit 1
fi
# Axum coordinates calls only; media must remain browser WebRTC/TURN.
if grep -Eiq 'rtp packet|audio relay|video relay|media relay|ffmpeg|gstreamer' realtime/src/call_runtime.rs; then
  echo 'Call runtime appears to relay media.' >&2
  exit 1
fi

python3 -m py_compile apps/chat/models.py apps/chat/services.py apps/chat/api/serializers.py apps/chat/api/views.py apps/chat/api/urls.py
python3 - <<'PY'
from pathlib import Path
import yaml
for name in ('docker-compose.yml', 'docker-compose.local.yml', 'docker-compose.production.yml'):
    yaml.safe_load(Path(name).read_text(encoding='utf-8'))
print('Compose YAML parsed.')
PY
sh -n scripts/stack-profile.sh
bash -n scripts/verify-axum-runtime.sh scripts/deploy-axum-cutover.sh
printf '%s\n' 'Axum call runtime source contracts passed.'
