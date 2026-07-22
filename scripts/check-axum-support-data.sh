#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

require() {
  pattern=$1
  file=$2
  grep -Fq "$pattern" "$file" || {
    echo "Missing Support data-plane contract: $pattern in $file" >&2
    exit 1
  }
}

require 'mod support_data;' realtime/src/main.rs
require 'mod support_signal_store;' realtime/src/main.rs
require 'SUPPORT_DATA_BACKEND' realtime/src/config.rs
require 'support_data_backend' realtime/src/main.rs
require '/api/v1/support-fast/conversations/' realtime/src/main.rs
require '/api/v1/support-fast/widget/{site_key}/sessions/{session_id}/messages/' realtime/src/main.rs
require 'support.message.created' realtime/src/support_data.rs
require 'support.conversation.created' realtime/src/support_data.rs
require 'support.call.signal' realtime/src/support_data.rs
require 'pg_advisory_xact_lock' realtime/src/support_data.rs
require 'FOR UPDATE' realtime/src/support_data.rs
require 'support_supportdataplanejob' realtime/src/support_data.rs
require 'support_widget_message_rate_per_minute' realtime/src/support_data.rs
require 'envSupportDataBackend === "axum"' frontend/src/lib/config.ts
require 'supportDataPath' frontend/src/api/support.ts
require 'data_plane_backend === "axum"' frontend/public/support-widget/v1/widget.js
require 'SupportDataPlaneJob' apps/support/models.py
require 'sup_data_site_stat_upd_idx' apps/support/models.py
require 'sup_data_agent_stat_upd_idx' apps/support/models.py
require 'sup_data_upload_conv_idx' apps/support/models.py
require 'process_due_support_data_plane_jobs' apps/support/data_plane_jobs.py
require 'cleanup_completed_support_data_plane_jobs' apps/support/data_plane_jobs.py
require '0024_support_data_plane_jobs' scripts/verify-axum-runtime.sh
require 'support-shadow)' scripts/stack-profile.sh
require 'support)' scripts/stack-profile.sh
require 'support-rollback)' scripts/stack-profile.sh
require 'REALTIME_EPHEMERAL_BACKEND=nats SUPPORT_DATA_BACKEND=axum' scripts/stack-profile.sh

# Axum may attach already-approved uploads, but upload authorization, scanning,
# private object serving, billing, knowledge, and workflow administration remain Django-owned.
if grep -Eiq 'clamav|antivirus scan|ffmpeg|put_object|multipart upload|subscription webhook|billing webhook' realtime/src/support_data.rs; then
  echo 'Axum Support data plane contains responsibilities that must remain outside the realtime process.' >&2
  exit 1
fi

python3 -m py_compile \
  apps/support/models.py \
  apps/support/data_plane_jobs.py \
  apps/support/migrations/0023_support_data_plane_indexes.py \
  apps/support/migrations/0024_support_data_plane_jobs.py \
  apps/support/tasks.py \
  apps/support/api/serializers.py \
  apps/support/api/views.py \
  config/celery.py \
  config/settings.py
python3 - <<'PY'
from pathlib import Path
import yaml
for name in ('docker-compose.yml', 'docker-compose.local.yml', 'docker-compose.production.yml'):
    yaml.safe_load(Path(name).read_text(encoding='utf-8'))
print('Compose YAML parsed.')
PY
sh -n scripts/stack-profile.sh scripts/check-axum-support-data.sh
bash -n scripts/verify-axum-runtime.sh scripts/deploy-axum-cutover.sh
node --check frontend/public/support-widget/v1/widget.js
node frontend/scripts/check-support-data-backend-source.mjs
printf '%s\n' 'Axum Support data-plane source contracts passed.'
