#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
duration="${1:-600}"
interval="${2:-5}"
output="${3:-loadtests/results/vps-metrics-$(date -u +%Y%m%dT%H%M%SZ).jsonl}"
mkdir -p "$(dirname "$output")"
exec python3 scripts/capture_load_test_metrics.py --duration "$duration" --interval "$interval" --output "$output"
