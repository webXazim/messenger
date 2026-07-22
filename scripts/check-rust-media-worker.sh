#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

require() {
  local file=$1 pattern=$2 message=$3
  grep -Eq "$pattern" "$file" || { echo "$message" >&2; exit 1; }
}

require apps/chat/models.py 'class MediaProcessingJob' 'MediaProcessingJob model is missing'
require apps/chat/migrations/0024_rust_media_processing_jobs.py 'chat_media_job_status_due_idx' 'Media worker migration/index is missing'
require apps/chat/services.py 'MEDIA_PROCESSING_BACKEND' 'Upload scanning is not routed to the media worker'
require apps/chat/services.py 'enqueue_media_processing' 'Clean uploads are not durably enqueued'
require apps/chat/media_processing.py 'select_for_update' 'Media job enqueue/fallback locking is missing'
require media-worker/src/db.rs 'FOR UPDATE OF j SKIP LOCKED' 'Rust worker does not use concurrency-safe job claiming'
require media-worker/src/db.rs 'lease_token' 'Rust worker lease protection is missing'
require media-worker/src/process.rs 'ffprobe' 'Rust worker metadata probing is missing'
require media-worker/src/process.rs 'generate_thumbnail' 'Rust worker thumbnail orchestration is missing'
require media-worker/src/process.rs 'generate_waveform' 'Rust worker waveform generation is missing'
require media-worker/src/process.rs 'max_frame_pixels' 'Rust worker decompression guard is missing'
require media-worker/src/db.rs 'complete_shadow_job' 'Non-invasive media shadow completion is missing'
require media-worker/src/main.rs 'shadow_mode' 'Media shadow mode is not isolated from production writes'
require media-worker/src/storage.rs 'AmazonS3Builder' 'Private R2/S3 support is missing'
require media-worker/src/storage.rs 'Local' 'Local private-media support is missing'
require docker-compose.yml '^  media-worker:' 'Media worker Compose service is missing'
require docker-compose.yml 'mem_limit: 256m' 'Media worker VPS memory limit is missing'
require scripts/stack-profile.sh 'media-shadow' 'Media worker shadow rollout profile is missing'
require scripts/stack-profile.sh 'media-rollback' 'Media worker rollback profile is missing'

if grep -Eiq 'clamav|antivirus|scan_file_field' media-worker/src/*.rs; then
  echo 'Rust media worker must not replace Django/ClamAV antivirus authorization.' >&2
  exit 1
fi
if grep -Eiq 'ffmpeg|ffprobe|std::process::Command|tokio::process::Command' realtime/src/*.rs; then
  echo 'CPU-heavy media processing leaked into the Axum realtime process.' >&2
  exit 1
fi

python3 -m py_compile \
  apps/chat/models.py apps/chat/services.py apps/chat/tasks.py apps/chat/media_processing.py \
  apps/chat/migrations/0024_rust_media_processing_jobs.py config/settings.py config/celery.py
bash -n scripts/generate-media-worker-lockfile.sh
sh -n scripts/stack-profile.sh

python3 - <<'PY'
from pathlib import Path
for path in Path('media-worker/src').glob('*.rs'):
    text = path.read_text(encoding='utf-8')
    pairs = {'{': '}', '(': ')', '[': ']'}
    stack = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in pairs:
            stack.append((char, pairs[char]))
        elif char in pairs.values():
            if not stack or stack[-1][1] != char:
                raise SystemExit(f'{path}: unbalanced {char}')
            stack.pop()
    if stack:
        raise SystemExit(f'{path}: unclosed delimiters')
print('Rust media worker structural checks passed.')
PY

echo 'Rust media worker source contracts passed.'
