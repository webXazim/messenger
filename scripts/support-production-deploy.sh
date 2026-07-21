#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
echo "Support Chat uses the unified production stack; running the complete deployment workflow."
exec bash ./scripts/deploy-production.sh "$@"
