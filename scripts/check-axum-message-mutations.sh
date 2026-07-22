#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

require() {
  pattern=$1
  file=$2
  grep -Fq "$pattern" "$file" || {
    echo "Missing message mutation contract: $pattern in $file" >&2
    exit 1
  }
}

require 'mod message_mutations;' realtime/src/main.rs
require 'CHAT_MESSAGE_MUTATION_BACKEND' realtime/src/config.rs
require 'chat_message_mutation_backend' realtime/src/main.rs
require '/messages/{message_id}/manage/' realtime/src/main.rs
require '/messages/{message_id}/restore/' realtime/src/main.rs
require '/messages/{message_id}/retry/' realtime/src/main.rs
require 'FOR UPDATE OF m, cp' realtime/src/message_mutations.rs
require "deletion_source = 'sender'" realtime/src/message_mutations.rs
require 'message.deletion_source != "sender"' realtime/src/message_mutations.rs
require "'metadata', CASE WHEN m.is_deleted" realtime/src/chat_reads.rs
require 'AND NOT m.is_deleted' realtime/src/chat_reads.rs
require 'message__is_deleted=False' apps/chat/services.py
require 'chat_messageedithistory' realtime/src/message_mutations.rs
require 'common_realtimeoutboxevent' realtime/src/message_mutations.rs
require 'message.updated' realtime/src/message_mutations.rs
require 'message.deleted' realtime/src/message_mutations.rs
require 'message.restored' realtime/src/message_mutations.rs
require 'message.retried' realtime/src/message_mutations.rs
require 'deleted_text_backup' apps/chat/migrations/0022_message_sender_restore_state.py
require 'VITE_CHAT_MESSAGE_MUTATION_BACKEND' frontend/src/lib/config.ts
require 'mutations-shadow' scripts/stack-profile.sh
require 'mutations)' scripts/stack-profile.sh
require 'CHAT_MESSAGE_MUTATION_BACKEND=axum' scripts/stack-profile.sh
require 'chat_message_mutation_backend' scripts/verify-axum-runtime.sh
require 'expected_message_mutation_backend' scripts/deploy-axum-cutover.sh

python3 -m py_compile \
  apps/chat/models.py \
  apps/chat/services.py \
  apps/chat/api/serializers.py \
  apps/chat/api/views.py \
  apps/chat/api/urls.py \
  apps/chat/migrations/0022_message_sender_restore_state.py \
  apps/chat/tests_message_restore.py
sh -n scripts/stack-profile.sh
bash -n scripts/verify-axum-runtime.sh scripts/deploy-axum-cutover.sh
printf '%s\n' 'Axum message mutation source contracts passed.'
