#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
key_file="${BACKUP_KEY_FILE:-secrets/backup-passphrase}"
mkdir -p "$(dirname "$key_file")"
if [[ -e "$key_file" ]]; then
  echo "Backup key already exists: $key_file"
  exit 0
fi
umask 077
openssl rand -base64 72 > "$key_file"
chmod 0600 "$key_file"
echo "Created $key_file"
echo "Copy this key to a secure offline password manager now. Backups cannot be restored without it."
