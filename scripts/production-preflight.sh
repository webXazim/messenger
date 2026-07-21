#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
echo "This deployment entry point now uses the unified production workflow."
exec bash ./scripts/deploy-production.sh "$@"
