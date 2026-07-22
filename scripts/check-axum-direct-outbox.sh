#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

python3 -m compileall -q config apps/common
python3 - <<'PY'
from pathlib import Path
required = {
    'realtime/src/nats_durable_publish.rs': ['Nats', 'message_id', 'ack.sequence', 'subject_for'],
    'realtime/src/command_delivery.rs': ['mark_outbox_published', 'mark_outbox_publish_failed', 'deliver_local_once'],
    'realtime/src/database.rs': ['nats_jetstream_axum', "status = 'published'"],
    'apps/common/realtime.py': ['REALTIME_OUTBOX_PUBLISHER'],
    'apps/common/tasks.py': ['publish_realtime_outbox_events.apply_async', '_update_claimed_outbox_row', 'last_error=claim_marker'],
}
for filename, tokens in required.items():
    text = Path(filename).read_text(encoding='utf-8')
    missing = [token for token in tokens if token not in text]
    if missing:
        raise SystemExit(f'{filename}: missing {missing}')
print('direct outbox source contracts passed')
PY
python3 - <<'PY'
import yaml
for filename in ('docker-compose.yml', 'docker-compose.production.yml', 'docker-compose.local.yml'):
    with open(filename, encoding='utf-8') as handle:
        yaml.safe_load(handle)
print('compose yaml passed')
PY
sh -n scripts/stack-profile.sh scripts/verify-axum-runtime.sh
printf 'Axum direct outbox checks passed.\n'
