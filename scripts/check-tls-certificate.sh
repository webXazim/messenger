#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
cert="${1:-secrets/tls/origin.crt}"
key="${2:-secrets/tls/origin.key}"
domain="${3:-}"
minimum_days="${TLS_MINIMUM_VALID_DAYS:-30}"

if [[ -z "$domain" && -f .env ]]; then
  domain="$(sed -n 's/^APP_DOMAIN=//p' .env | tail -n 1 | tr -d '\r"' | tr -d "'")"
fi

[[ -s "$cert" ]] || { echo "TLS certificate not found: $cert" >&2; exit 1; }
[[ -s "$key" ]] || { echo "TLS private key not found: $key" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required." >&2; exit 1; }

seconds=$((minimum_days * 86400))
openssl x509 -in "$cert" -noout >/dev/null
openssl pkey -in "$key" -noout >/dev/null
openssl x509 -checkend "$seconds" -noout -in "$cert" >/dev/null || {
  echo "TLS certificate expires in fewer than $minimum_days days." >&2
  exit 1
}

cert_key_hash="$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
private_key_hash="$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
[[ "$cert_key_hash" == "$private_key_hash" ]] || {
  echo "TLS certificate and private key do not match." >&2
  exit 1
}

if [[ -n "$domain" ]]; then
  openssl x509 -in "$cert" -noout -checkhost "$domain" >/dev/null || {
    echo "TLS certificate does not cover APP_DOMAIN=$domain." >&2
    exit 1
  }
fi

subject="$(openssl x509 -in "$cert" -noout -subject | sed 's/^subject=//')"
expiry="$(openssl x509 -in "$cert" -noout -enddate | cut -d= -f2-)"
echo "TLS certificate valid: $subject"
echo "Expires: $expiry"
