#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f realtime/Cargo.toml ]] || { echo "Missing realtime/Cargo.toml" >&2; exit 1; }

if [[ -s realtime/Cargo.lock ]]; then
  echo "realtime/Cargo.lock already exists."
  exit 0
fi

command -v docker >/dev/null || { echo "Docker is required to generate realtime/Cargo.lock." >&2; exit 1; }

# Resolve the dependency graph before the cutover. Running as the invoking user
# avoids leaving a root-owned lockfile in the release directory.
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e CARGO_HOME=/tmp/cargo \
  -e CARGO_NET_RETRY=5 \
  -v "$PWD/realtime:/app" \
  -w /app \
  rust:1.86-bookworm \
  cargo generate-lockfile

test -s realtime/Cargo.lock || { echo "Cargo.lock generation failed." >&2; exit 1; }
echo "Generated realtime/Cargo.lock. Keep it with this release and future upgrades."
