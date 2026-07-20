#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

./scripts/check-support-upgrade-baseline.sh
(
  cd frontend
  npm run check:support-design-system
  npm run build
)

echo "Support Chat Upgrade 02 design-system checks passed."
