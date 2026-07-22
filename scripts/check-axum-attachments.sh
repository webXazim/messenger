#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

require() {
  pattern=$1
  file=$2
  grep -Fq "$pattern" "$file" || {
    echo "Missing attachment-plane contract: $pattern in $file" >&2
    exit 1
  }
}

require 'mod attachments;' realtime/src/main.rs
require 'CHAT_ATTACHMENT_BACKEND' realtime/src/config.rs
require 'chat_attachment_backend' realtime/src/main.rs
require '/attachment-messages/' realtime/src/main.rs
require '/attachments/{attachment_id}/media-token/' realtime/src/main.rs
require "scan_status='clean'" realtime/src/commands.rs
require "status='pending'" realtime/src/commands.rs
require 'FOR UPDATE' realtime/src/commands.rs
require "UPDATE chat_pendingupload SET status='attached'" realtime/src/commands.rs
require "COALESCE(metadata,'{}'::jsonb)||" realtime/src/commands.rs
require 'Algorithm::HS256' realtime/src/attachments.rs
require 'MEDIA_TOKEN_SHARED_SECRET' realtime/src/config.rs
require 'jwt.decode' apps/chat/services.py
require 'algorithms=["HS256"]' apps/chat/services.py
require 'CHAT_ATTACHMENT_BACKEND === "axum"' frontend/src/api/chat.ts
require 'send_chat_message' realtime/src/attachments.rs
require 'VITE_CHAT_ATTACHMENT_BACKEND' frontend/src/lib/config.ts
require 'attachments-shadow' scripts/stack-profile.sh
require 'attachments)' scripts/stack-profile.sh
require 'attachments-rollback)' scripts/stack-profile.sh
require 'replace-with-at-least-32-random-characters' realtime/src/config.rs
require 'chat_attachment_backend' scripts/verify-axum-runtime.sh

# Rust may only attach already-scanned objects; upload authorization, scanning,
# object creation and processing must remain in Django/Celery.
if grep -Eiq 'clamav|antivirus|multipart|s3 put|put_object|thumbnail generation|ffmpeg' realtime/src/attachments.rs; then
  echo 'Axum attachment plane contains upload/scanning/processing responsibilities.' >&2
  exit 1
fi

python3 -m py_compile config/settings.py apps/chat/services.py apps/chat/api/views.py apps/chat/api/serializers.py
python3 - <<'PY'
from pathlib import Path
import yaml
for name in ('docker-compose.yml', 'docker-compose.local.yml', 'docker-compose.production.yml'):
    yaml.safe_load(Path(name).read_text(encoding='utf-8'))
print('Compose YAML parsed.')
PY
sh -n scripts/stack-profile.sh scripts/check-axum-attachments.sh
bash -n scripts/verify-axum-runtime.sh scripts/deploy-axum-cutover.sh
node frontend/scripts/check-attachment-backend-source.mjs
printf '%s\n' 'Axum approved-attachment source contracts passed.'
