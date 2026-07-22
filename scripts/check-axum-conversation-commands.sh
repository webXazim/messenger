#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

require() {
  pattern=$1
  file=$2
  grep -Fq "$pattern" "$file" || {
    echo "Missing conversation-command contract: $pattern in $file" >&2
    exit 1
  }
}

require 'mod conversation_commands;' realtime/src/main.rs
require 'CHAT_CONVERSATION_COMMAND_BACKEND' realtime/src/config.rs
require 'chat_conversation_command_backend' realtime/src/main.rs
require '/conversations/{conversation_id}/draft/' realtime/src/main.rs
require '/transfer-ownership/' realtime/src/main.rs
require '/blocks/{user_id}/' realtime/src/main.rs
require 'FOR UPDATE' realtime/src/conversation_commands.rs
require 'pg_advisory_xact_lock' realtime/src/conversation_commands.rs
require 'direct_key_for_users' realtime/src/conversation_commands.rs
require 'deserialize_present_optional' realtime/src/conversation_commands.rs
require 'conversation.participants_added' realtime/src/conversation_commands.rs
require 'schedule_conversation_cleanup' realtime/src/conversation_commands.rs
require 'central_payments_enabled' realtime/src/conversation_commands.rs
require 'has_block_relationship' realtime/src/conversation_commands.rs
require 'direct conversation is blocked' realtime/src/commands.rs
require 'direct conversation is blocked' realtime/src/attachments.rs
require 'direct conversation is blocked' realtime/src/call_commands.rs
require 'can_emit_messenger_ephemeral' realtime/src/websocket.rs
require 'chat_userblock ub' realtime/src/call_runtime.rs
require 'CHAT_CONVERSATION_COMMAND_BACKEND === "axum"' frontend/src/api/chat.ts
require 'VITE_CHAT_CONVERSATION_COMMAND_BACKEND' frontend/src/lib/config.ts
require 'conversations-shadow' scripts/stack-profile.sh
require 'conversations)' scripts/stack-profile.sh
require 'conversations-rollback)' scripts/stack-profile.sh
require 'chat_conversation_command_backend' scripts/verify-axum-runtime.sh

# Conversation commands must not take over object deletion, antivirus, uploads,
# billing webhooks, or media processing.
if grep -Eiq 'clamav|antivirus|ffmpeg|put_object|billing webhook|subscription webhook' realtime/src/conversation_commands.rs; then
  echo 'Axum conversation commands contain responsibilities that must remain outside the realtime data plane.' >&2
  exit 1
fi

python3 -m py_compile apps/chat/api/views.py apps/chat/api/serializers.py apps/chat/services.py config/settings.py
python3 - <<'PY'
from pathlib import Path
import yaml
for name in ('docker-compose.yml', 'docker-compose.local.yml', 'docker-compose.production.yml'):
    yaml.safe_load(Path(name).read_text(encoding='utf-8'))
print('Compose YAML parsed.')
PY
sh -n scripts/stack-profile.sh scripts/check-axum-conversation-commands.sh
bash -n scripts/verify-axum-runtime.sh scripts/deploy-axum-cutover.sh
node frontend/scripts/check-conversation-command-backend-source.mjs
printf '%s\n' 'Axum conversation-command source contracts passed.'
