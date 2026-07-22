#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

require() {
  pattern=$1
  file=$2
  grep -Fq "$pattern" "$file" || {
    echo "Missing interaction contract: $pattern in $file" >&2
    exit 1
  }
}

require 'mod message_interactions;' realtime/src/main.rs
require 'CHAT_INTERACTION_BACKEND' realtime/src/config.rs
require 'chat_interaction_backend' realtime/src/main.rs
require '/mark-delivered/' realtime/src/main.rs
require '/mark-read/' realtime/src/main.rs
require '/reactions/' realtime/src/main.rs
require 'FOR UPDATE OF cp' realtime/src/message_interactions.rs
require 'participant is blocked' realtime/src/message_interactions.rs
require 'ON CONFLICT (message_id, user_id) DO NOTHING' realtime/src/message_interactions.rs
require "message_has_reactions" realtime/src/message_interactions.rs
require 'common_realtimeoutboxevent' realtime/src/message_interactions.rs
require 'message.reaction_updated' realtime/src/message_interactions.rs
require 'VITE_CHAT_INTERACTION_BACKEND' frontend/src/lib/config.ts
require 'interactions-shadow' scripts/stack-profile.sh
require 'interactions)' scripts/stack-profile.sh
require 'CHAT_COMMAND_BACKEND=django CHAT_INTERACTION_BACKEND=axum' scripts/stack-profile.sh

sh -n scripts/stack-profile.sh
printf '%s\n' 'Axum receipt and reaction source contracts passed.'
