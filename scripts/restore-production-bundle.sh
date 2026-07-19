#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bundle="${1:-}"
mode="${2:-}"
[[ -f "$bundle" ]] || { echo "Usage: $0 backups/messenger-system-*.tar.gz.enc --extract-only|--confirm" >&2; exit 2; }
[[ "$mode" == "--extract-only" || "$mode" == "--confirm" ]] || { echo "Choose --extract-only or --confirm." >&2; exit 2; }
key_file="${BACKUP_KEY_FILE:-secrets/backup-passphrase}"
[[ -s "$key_file" ]] || { echo "Missing backup key: $key_file" >&2; exit 1; }
[[ -f "${bundle}.sha256" ]] && sha256sum -c "${bundle}.sha256"
if [[ -f "${bundle}.hmac" ]]; then
  python3 - "$key_file" "$bundle" "${bundle}.hmac" <<'PYHMAC'
import hashlib, hmac, sys
key=open(sys.argv[1],'rb').read().strip()
expected=open(sys.argv[3]).read().strip()
digest=hmac.new(key, digestmod=hashlib.sha256)
with open(sys.argv[2],'rb') as fh:
    for chunk in iter(lambda: fh.read(1024*1024), b''):
        digest.update(chunk)
if not hmac.compare_digest(expected, digest.hexdigest()):
    raise SystemExit('Encrypted backup HMAC verification failed')
print('Encrypted backup HMAC verification passed.')
PYHMAC
fi
work="$(mktemp -d backups/restore.XXXXXX)"
trap '[[ "$mode" == "--extract-only" ]] || rm -rf "$work"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:${key_file}" \
  -in "$bundle" | tar -xzf - -C "$work" --no-same-owner --no-same-permissions
python3 - "$work" <<'PY'
import json, hashlib, sys
from pathlib import Path
root=Path(sys.argv[1])
manifest=json.loads((root/'manifest.json').read_text())
for field in ('database','media','configuration'):
    item=manifest.get(field)
    if not item: continue
    path=root/item['name']
    digest=hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024*1024), b''):
            digest.update(chunk)
    h=digest.hexdigest()
    if h != item['sha256']:
        raise SystemExit(f"Checksum mismatch: {path.name}")
print('Bundle manifest and component checksums passed.')
PY
if [[ "$mode" == "--extract-only" ]]; then
  echo "Extracted to $work"
  echo "Review the configuration archive before restoring the database."
  trap - EXIT
  exit 0
fi

db="$(find "$work" -maxdepth 1 -name 'messenger-*.dump' -print -quit)"
media="$(find "$work" -maxdepth 1 -name 'messenger-media-*.tar.gz' -print -quit)"
[[ -n "$db" ]] || { echo "Database dump missing from bundle." >&2; exit 1; }
./scripts/restore-postgres.sh "$db" --confirm
[[ -n "$media" ]] && ./scripts/restore-media.sh "$media" --confirm
config="$(find "$work" -maxdepth 1 -name 'messenger-config-*.tar.gz' -print -quit)"
[[ -n "$config" ]] && cp "$config" backups/recovered-configuration.tar.gz
./scripts/production-readiness.sh --probe
echo "System restore completed. Recovered configuration was copied for manual review and was not automatically installed."
