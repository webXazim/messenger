#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

[[ -f media-worker/Cargo.toml ]] || { echo "Missing media-worker/Cargo.toml" >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is required to generate media-worker/Cargo.lock." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required." >&2; exit 1; }

run_cargo() {
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e CARGO_HOME=/tmp/cargo \
    -e CARGO_NET_RETRY=5 \
    -v "$ROOT/media-worker:/app" \
    -w /app \
    rust:1.88-bookworm \
    "$@"
}

if [[ -s media-worker/Cargo.lock ]]; then
  echo "Checking media-worker/Cargo.lock against Cargo.toml..."
  if run_cargo cargo metadata --locked --format-version 1 >/dev/null; then
    echo "media-worker/Cargo.lock is current."
    exit 0
  fi
  echo "media-worker/Cargo.lock is stale; regenerating it."
  rm -f media-worker/Cargo.lock
fi

run_cargo cargo generate-lockfile
test -s media-worker/Cargo.lock || { echo "Cargo.lock generation failed." >&2; exit 1; }
echo "Generated current media-worker/Cargo.lock. Keep it with this deployment."
