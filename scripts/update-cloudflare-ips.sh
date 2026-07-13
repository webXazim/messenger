#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
output="nginx/cloudflare-real-ip.conf"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

fetch_ranges() {
  local url="$1"
  curl --fail --silent --show-error --location --max-time 20 "$url"
}

{
  echo "# Generated from Cloudflare's published proxy ranges."
  echo "# Do not edit manually; run scripts/update-cloudflare-ips.sh."
  echo "set_real_ip_from 127.0.0.1;"
  echo "set_real_ip_from ::1;"
  while IFS= read -r network; do
    [[ -z "$network" ]] && continue
    if [[ ! "$network" =~ ^[0-9A-Fa-f:.]+/[0-9]{1,3}$ ]]; then
      echo "Unexpected Cloudflare network value: $network" >&2
      exit 1
    fi
    printf 'set_real_ip_from %s;\n' "$network"
  done < <(
    fetch_ranges https://www.cloudflare.com/ips-v4
    echo
    fetch_ranges https://www.cloudflare.com/ips-v6
  )
} > "$tmp"

range_count="$(grep -c '^set_real_ip_from ' "$tmp")"
if (( range_count < 10 )); then
  echo "Cloudflare IP update returned too few ranges ($range_count)." >&2
  exit 1
fi

mv "$tmp" "$output"
trap - EXIT
echo "Updated $output with $range_count trusted proxy ranges."
