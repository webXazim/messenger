#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

[[ -f realtime/Cargo.toml ]] || { echo "Missing realtime/Cargo.toml" >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is required to generate realtime/Cargo.lock." >&2; exit 1; }

docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose v2 is required." >&2
  exit 1
}

run_cargo() {
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e CARGO_HOME=/tmp/cargo \
    -e CARGO_NET_RETRY=5 \
    -v "$ROOT/realtime:/app" \
    -w /app \
    rust:1.88-bookworm \
    "$@"
}

# Keep an existing lockfile only when it exactly matches Cargo.toml.
# `cargo metadata --locked --no-deps` is a cheap consistency check and does
# not compile the realtime service.
if [[ -s realtime/Cargo.lock ]]; then
  echo "Checking realtime/Cargo.lock against Cargo.toml..."
  if run_cargo cargo metadata --locked --no-deps --format-version 1 >/dev/null; then
    echo "realtime/Cargo.lock is current."
    exit 0
  fi

  echo "realtime/Cargo.lock is stale; regenerating it."
  rm -f realtime/Cargo.lock
fi

run_cargo cargo generate-lockfile

test -s realtime/Cargo.lock || {
  echo "Cargo.lock generation failed." >&2
  exit 1
}

echo "Generated current realtime/Cargo.lock. Keep it with this deployment."
