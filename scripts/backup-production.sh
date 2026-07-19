#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

read_env() {
  local key="$1" default="${2:-}" value
  value="$(sed -n "s/^${key}=//p" .env | tail -n 1 | tr -d '\r')"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value:-$default}"
}

key_file="$(read_env BACKUP_KEY_FILE secrets/backup-passphrase)"
[[ -s "$key_file" ]] || { echo "Missing backup encryption key: $key_file. Run scripts/generate-backup-key.sh." >&2; exit 1; }
mkdir -p backups
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work="$(mktemp -d backups/.bundle-${stamp}.XXXXXX)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

capture_path() {
  local command="$1" output path
  output="$(bash "$command")"
  printf '%s\n' "$output" >&2
  path="$(printf '%s\n' "$output" | sed -n 's/^Created and verified //p' | tail -n 1)"
  [[ -n "$path" && -f "$path" ]] || { echo "Could not locate backup produced by $command" >&2; exit 1; }
  printf '%s' "$path"
}

db_backup="$(capture_path ./scripts/backup-postgres.sh)"
media_backup=""
if [[ "$(read_env BACKUP_INCLUDE_MEDIA true)" =~ ^([Tt]rue|1|yes|on)$ ]]; then
  media_backup="$(capture_path ./scripts/backup-media.sh)"
fi

config_archive="backups/messenger-config-${stamp}.tar.gz"
config_files=(.env)
for path in secrets/realtime-private.pem secrets/realtime-public.pem secrets/tls/origin.crt secrets/tls/origin.key; do
  [[ -f "$path" ]] && config_files+=("$path")
done
tar -czf "$config_archive" "${config_files[@]}"
sha256sum "$config_archive" > "${config_archive}.sha256"

python3 - "$work/manifest.json" "$stamp" "$db_backup" "$media_backup" "$config_archive" <<'PY'
import hashlib, json, os, platform, sys
from pathlib import Path
out, stamp, db, media, config = sys.argv[1:]
def info(path):
    if not path:
        return None
    p=Path(path)
    h=hashlib.sha256()
    with p.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024*1024), b''):
            h.update(chunk)
    return {'name': p.name, 'bytes': p.stat().st_size, 'sha256': h.hexdigest()}
payload={
    'format': 1,
    'created_at_utc': stamp,
    'hostname': platform.node(),
    'database': info(db),
    'media': info(media),
    'configuration': info(config),
}
Path(out).write_text(json.dumps(payload, indent=2, sort_keys=True))
PY

cp "$db_backup" "$work/"
( cd "$work" && sha256sum "$(basename "$db_backup")" > "$(basename "$db_backup").sha256" )
if [[ -n "$media_backup" ]]; then
  cp "$media_backup" "$work/"
  ( cd "$work" && sha256sum "$(basename "$media_backup")" > "$(basename "$media_backup").sha256" )
fi
cp "$config_archive" "$work/"
( cd "$work" && sha256sum "$(basename "$config_archive")" > "$(basename "$config_archive").sha256" )

bundle="backups/messenger-system-${stamp}.tar.gz"
tar -C "$work" -czf "$bundle" .
encrypted="${bundle}.enc"
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
  -pass "file:${key_file}" -in "$bundle" -out "${encrypted}.partial"
mv "${encrypted}.partial" "$encrypted"
sha256sum "$encrypted" > "${encrypted}.sha256"
python3 - "$key_file" "$encrypted" "${encrypted}.hmac" <<'PYHMAC'
import hashlib, hmac, sys
key=open(sys.argv[1],'rb').read().strip()
digest=hmac.new(key, digestmod=hashlib.sha256)
with open(sys.argv[2],'rb') as fh:
    for chunk in iter(lambda: fh.read(1024*1024), b''):
        digest.update(chunk)
open(sys.argv[3],'w').write(digest.hexdigest()+'\n')
PYHMAC
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:${key_file}" \
  -in "$encrypted" | tar -tzf - >/dev/null
chmod 0600 "$encrypted" "${encrypted}.sha256" "${encrypted}.hmac"

if [[ "$(read_env BACKUP_R2_ENABLED false)" =~ ^([Tt]rue|1|yes|on)$ ]]; then
  bucket="$(read_env BACKUP_R2_BUCKET_NAME)"
  [[ -n "$bucket" ]] || { echo "BACKUP_R2_ENABLED is true but BACKUP_R2_BUCKET_NAME is empty." >&2; exit 1; }
  sha="$(cut -d' ' -f1 "${encrypted}.sha256")"
  hmac_sha="$(tr -d '\r\n' < "${encrypted}.hmac")"
  compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
  cat "$encrypted" | "${compose[@]}" exec -T web python manage.py upload_system_backup \
    --object-name "$(basename "$encrypted")" --sha256 "$sha" --hmac-sha256 "$hmac_sha"
fi

retention_days="$(read_env BACKUP_LOCAL_RETENTION_DAYS 7)"
if [[ "$retention_days" =~ ^[0-9]+$ ]]; then
  find backups -maxdepth 1 -type f \
    \( -name 'messenger-system-*.tar.gz.enc' -o -name 'messenger-system-*.tar.gz.enc.sha256' -o -name 'messenger-system-*.tar.gz.enc.hmac' \) \
    -mtime "+${retention_days}" -delete
fi

if [[ ! "$(read_env BACKUP_KEEP_PLAINTEXT false)" =~ ^([Tt]rue|1|yes|on)$ ]]; then
  rm -f "$bundle" "$db_backup" "${db_backup}.sha256" "$config_archive" "${config_archive}.sha256"
  [[ -n "$media_backup" ]] && rm -f "$media_backup" "${media_backup}.sha256"
fi

echo "Created, encrypted, and verified $encrypted"
echo "Keep $key_file outside this VPS; the encrypted backup is unusable without it."
