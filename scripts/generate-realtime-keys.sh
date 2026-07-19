#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${1:-${ROOT_DIR}/secrets}"
PRIVATE_KEY="${SECRETS_DIR}/realtime-private.pem"
PUBLIC_KEY="${SECRETS_DIR}/realtime-public.pem"

mkdir -p "${SECRETS_DIR}"
if [[ -e "${PRIVATE_KEY}" || -e "${PUBLIC_KEY}" ]]; then
  echo "Refusing to overwrite an existing realtime key. Remove or rotate it explicitly." >&2
  exit 1
fi

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${PRIVATE_KEY}"
openssl pkey -in "${PRIVATE_KEY}" -pubout -out "${PUBLIC_KEY}"
chmod 600 "${PRIVATE_KEY}"
chmod 644 "${PUBLIC_KEY}"

echo "Created:"
echo "  ${PRIVATE_KEY}"
echo "  ${PUBLIC_KEY}"
