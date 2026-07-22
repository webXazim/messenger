#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

fail() { echo "ERROR: $*" >&2; exit 1; }
require() {
  file=$1
  pattern=$2
  message=$3
  grep -Fq "$pattern" "$file" || fail "$message"
}
reject() {
  file=$1
  pattern=$2
  message=$3
  if grep -Fq "$pattern" "$file"; then fail "$message"; fi
}

require realtime/src/commands.rs "pub(crate) async fn send_chat_message" "Universal SQLx message transaction is missing"
for field in reply_to_id attachment_ids view_once_attachment_ids attachment_encryption entities is_voice_note waveform transcript_text; do
  require realtime/src/commands.rs "pub(crate) $field" "Universal message request is missing $field"
done
require realtime/src/commands.rs "chat_pendingupload" "Approved-upload locking is missing from the universal message transaction"
require realtime/src/commands.rs "chat_messagetranscript" "Transcript persistence is missing from the universal message transaction"
require realtime/src/commands.rs "chat_chatdataplanejob" "Durable chat control-plane job handoff is missing"
require realtime/src/commands.rs "message_created:" "Message-created job deduplication is missing"
require realtime/src/attachments.rs "send_chat_message" "Attachment messages are not using the universal transaction"
reject realtime/src/attachments.rs "django_fallback_required" "Attachment messages still contain a Django hot-path fallback"
reject realtime/src/commands.rs "django_fallback_required" "General message commands still contain a Django hot-path fallback"
require realtime/src/conversation_commands.rs "schedule_conversation_cleanup" "Conversation storage cleanup is not delegated durably"
require apps/chat/models.py "class ChatDataPlaneJob" "ChatDataPlaneJob model is missing"
require apps/chat/migrations/0023_chat_data_plane_jobs.py "name=\"ChatDataPlaneJob\"" "ChatDataPlaneJob migration is missing"
require apps/chat/data_plane_jobs.py "select_for_update(skip_locked=True)" "Chat data-plane job claiming is not concurrency-safe"
require apps/chat/data_plane_jobs.py "cleanup_conversation_if_unretained" "Deferred private-storage cleanup worker is missing"
require apps/chat/tasks.py "process_chat_data_plane_jobs" "Chat data-plane worker task is missing"
require config/celery.py '"process-chat-data-plane-jobs"' "Chat data-plane recovery schedule is missing"
require scripts/stack-profile.sh "prepare_chat_data_plane" "Cutover profiles do not apply the Django-owned migration first"
require scripts/stack-profile.sh "commands|hot-paths)" "Hot-path cutover alias is missing"
require realtime/src/command_auth.rs "config.chat_command_jwt_signing_key" "Axum command authentication is not using the access-token HMAC key"
require realtime/src/command_auth.rs "config.chat_command_jwt_public_key" "Axum command authentication is not using the access-token public key"
reject realtime/src/command_auth.rs "config.auth_public_key" "Axum command authentication still reuses the websocket-ticket public key"
require realtime/src/chat_reads.rs "read_pointer.sequence >= m.sequence" "Axum message reads do not derive durable read status from receipt pointers"
require realtime/src/chat_reads.rs "delivered_pointer.sequence >= m.sequence" "Axum message reads do not derive durable delivery status from receipt pointers"
require realtime/src/message_interactions.rs "target.sequence > current.sequence" "Axum receipt pointers are not advanced by durable message sequence"
require docker-compose.yml 'CHAT_COMMAND_JWT_ALGORITHM: ${CHAT_COMMAND_JWT_ALGORITHM:-${AUTH_PAYMENT_JWT_ALGORITHM:-HS256}}' "Compose does not inherit the Django access-token algorithm for Axum"
require docker-compose.yml 'CHAT_COMMAND_JWT_AUDIENCE: ${CHAT_COMMAND_JWT_AUDIENCE:-${AUTH_PAYMENT_JWT_AUDIENCE:-}}' "Compose does not inherit the Django access-token audience for Axum"
require scripts/deploy-axum-cutover.sh "Axum rejected a Django-issued access token" "Production cutover does not test Django-to-Axum access-token compatibility"

if command -v node >/dev/null 2>&1; then
  node frontend/scripts/check-attachment-backend-source.mjs
  node frontend/scripts/check-conversation-command-backend-source.mjs
  node frontend/scripts/check-hot-path-backend-source.mjs
else
  printf '%s\n' 'Node.js is not installed; skipping optional frontend source checks.'
fi

python3 - <<'PY'
from pathlib import Path
text = Path('frontend/src/api/chat.ts').read_text(encoding='utf-8')
start = text.index('  async sendMessage(')
end = text.index('\n  async getAttachment(', start)
block = text[start:end]
if 'isDjangoFallback' in block or 'django_fallback_required' in block or 'catch (' in block:
    raise SystemExit('Frontend message send still contains an automatic Django fallback')
if '/attachment-messages/' not in block or '/messages/' not in block:
    raise SystemExit('Frontend message send does not route both SQLx message paths')
print('Frontend universal-message source contract passed.')
PY

python3 - <<'PY'
import yaml
for name in ('docker-compose.yml', 'docker-compose.local.yml', 'docker-compose.production.yml'):
    with open(name, encoding='utf-8') as handle:
        yaml.safe_load(handle)
print('Compose YAML parsed.')
PY

python3 -m py_compile \
  apps/chat/models.py \
  apps/chat/data_plane_jobs.py \
  apps/chat/tasks.py \
  apps/chat/migrations/0023_chat_data_plane_jobs.py \
  config/celery.py \
  config/settings.py

sh -n scripts/stack-profile.sh
bash -n scripts/verify-axum-runtime.sh

echo "Axum hot-path consolidation source contracts passed."
