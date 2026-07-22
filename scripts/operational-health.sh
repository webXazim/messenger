#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
failures=()
warnings=()
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

read_env() {
  local key="$1" default="${2:-}" value
  value="$(sed -n "s/^${key}=//p" .env | tail -n 1 | tr -d '\r')"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value:-$default}"
}

services=(postgres pgbouncer redis nats web worker beat realtime frontend nginx)
media_backend="$(read_env MEDIA_PROCESSING_BACKEND django)"
if [[ "$media_backend" == "rust" || "$media_backend" == "rust_shadow" ]]; then services+=(media-worker); fi
for service in "${services[@]}"; do
  cid="$("${compose[@]}" ps -q "$service")"
  if [[ -z "$cid" ]]; then failures+=("$service container is missing"); continue; fi
  status="$(docker inspect -f '{{.State.Status}}' "$cid")"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")"
  [[ "$status" == running ]] || failures+=("$service status=$status")
  [[ "$health" == healthy || "$health" == none ]] || failures+=("$service health=$health")
done

disk_threshold="$(read_env OPS_DISK_MAX_PERCENT 85)"
disk_percent="$(df -P . | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
(( disk_percent < disk_threshold )) || failures+=("disk usage ${disk_percent}% exceeds ${disk_threshold}%")

min_available_mb="$(read_env OPS_MIN_AVAILABLE_MEMORY_MB 256)"
available_kb="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
available_mb="$((available_kb / 1024))"
(( available_mb >= min_available_mb )) || failures+=("available memory ${available_mb}MB is below ${min_available_mb}MB")

capacity_required="$(read_env OPS_REQUIRE_CAPACITY_REPORT false)"
verified_capacity_required="$(read_env REQUIRE_VERIFIED_CAPACITY_REPORT false)"
capacity_path="$(read_env OPS_CAPACITY_REPORT_PATH loadtests/results/capacity-report.json)"
capacity_max_age_days="$(read_env OPS_CAPACITY_REPORT_MAX_AGE_DAYS 7)"
if [[ "${capacity_required,,}" =~ ^(true|1|yes|on)$ || "${verified_capacity_required,,}" =~ ^(true|1|yes|on)$ ]]; then
  if [[ ! -f "$capacity_path" ]]; then
    failures+=("required capacity report is missing: $capacity_path")
  else
    current_fingerprint="$(python3 scripts/deployment_fingerprint.py --hash)"
    if ! capacity_output="$(python3 - "$capacity_path" .env "$capacity_max_age_days" "$current_fingerprint" 2>&1 <<'PY_CAPACITY'
import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
report_path=Path(sys.argv[1])
env_path=Path(sys.argv[2])
max_age_days=int(sys.argv[3])
current_fingerprint=sys.argv[4]
report=json.loads(report_path.read_text())
if report.get('schema_version') != 3 or not report.get('passed') or not report.get('verification_complete'):
    raise SystemExit('report_failed_or_incomplete')
recommended=int(report.get('recommended_production_max_connections') or 0)
match=re.search(r'^REALTIME_MAX_CONNECTIONS=([0-9]+)$', env_path.read_text(), re.M)
configured=int(match.group(1)) if match else 500
if recommended <= 0 or configured > recommended:
    raise SystemExit(f'capacity_exceeds_recommendation:{configured}>{recommended}')
valid_until=report.get('valid_until')
if not valid_until:
    raise SystemExit('report_missing_expiry')
expiry=datetime.fromisoformat(valid_until.replace('Z', '+00:00'))
if expiry.tzinfo is None:
    expiry=expiry.replace(tzinfo=timezone.utc)
if datetime.now(timezone.utc) >= expiry:
    raise SystemExit('report_expired')
if report.get('deployment_fingerprint') != current_fingerprint:
    raise SystemExit('deployment_fingerprint_changed')
age_days=(time.time()-report_path.stat().st_mtime)/86400
if age_days > max_age_days:
    raise SystemExit(f'report_file_too_old:{age_days:.1f}d')
print(f'configured={configured} recommended={recommended} valid_until={expiry.isoformat()}')
PY_CAPACITY
)"; then
      failures+=("capacity report is invalid or stale: ${capacity_output:-unknown error}")
    else
      echo "  capacity_report=$capacity_output"
    fi
  fi
fi

backup_max_age_hours="$(read_env OPS_BACKUP_MAX_AGE_HOURS 30)"
latest_backup="$(find backups -maxdepth 1 -type f -name 'messenger-system-*.tar.gz.enc' -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {$1=""; sub(/^ /,""); print}')"
if [[ -z "$latest_backup" ]]; then
  warnings+=("no encrypted system backup exists yet")
else
  age_hours="$(( ($(date +%s) - $(stat -c %Y "$latest_backup")) / 3600 ))"
  (( age_hours <= backup_max_age_hours )) || failures+=("latest backup is ${age_hours}h old; maximum is ${backup_max_age_hours}h")
  [[ -f "${latest_backup}.sha256" ]] && sha256sum -c "${latest_backup}.sha256" >/dev/null || failures+=("latest backup checksum is missing or invalid")
fi

"${compose[@]}" exec -T web python manage.py check_realtime_pipeline --json >"$tmpdir/realtime-pipeline.json" \
  || failures+=("Django outbox/JetStream pipeline is degraded")
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats >"$tmpdir/realtime-stats.json" \
  || failures+=("Axum internal stats endpoint failed")
"${compose[@]}" exec -T realtime curl -fsS http://127.0.0.1:9000/internal/metrics >"$tmpdir/realtime-metrics.txt" \
  || failures+=("Axum internal metrics endpoint failed")

if [[ -f "$tmpdir/realtime-stats.json" ]]; then
  if ! pressure_output="$(python3 - "$tmpdir/realtime-stats.json" "$(read_env OPS_AXUM_HIGH_QUEUE_MAX_PERCENT 75)" <<'PY_PRESSURE'
import json, sys
from pathlib import Path
stats=json.loads(Path(sys.argv[1]).read_text())
queue_limit=float(sys.argv[2])
http=stats.get('http_admission') or {}
pool=stats.get('sqlx_pool') or {}
queues=stats.get('websocket_queues') or {}
required_http=('read_limit','write_limit','in_flight','available_read','available_write')
if any(name not in http for name in required_http):
    raise SystemExit('missing_http_admission_metrics')
if not pool.get('enabled'):
    raise SystemExit('sqlx_pool_disabled')
maximum=int(pool.get('max_connections') or 0)
in_use=int(pool.get('in_use') or 0)
if maximum <= 0 or in_use > maximum:
    raise SystemExit(f'invalid_sqlx_pool:{in_use}/{maximum}')
read_limit=int(http['read_limit']); write_limit=int(http['write_limit']); in_flight=int(http['in_flight'])
if in_flight > read_limit + write_limit:
    raise SystemExit(f'invalid_http_admission:{in_flight}>{read_limit + write_limit}')
high_capacity=int(queues.get('high_capacity') or 0); high_queued=int(queues.get('high_queued') or 0)
ratio=(100.0*high_queued/high_capacity) if high_capacity else 0.0
print(f'http_in_flight={in_flight}/{read_limit + write_limit} sqlx_in_use={in_use}/{maximum} high_queue={ratio:.1f}%')
if ratio >= queue_limit:
    print(f'WARNING websocket high-priority queue is {ratio:.1f}% full', file=sys.stderr)
PY_PRESSURE
)"; then
    failures+=("Axum pressure metrics are invalid: ${pressure_output:-unknown error}")
  else
    echo "  axum_pressure=$pressure_output"
  fi
fi

pg_connections="$("${compose[@]}" exec -T postgres psql -U "$(read_env DB_USER)" -d "$(read_env DB_NAME)" -Atc "SELECT count(*) FROM pg_stat_activity;")"
pg_max="$("${compose[@]}" exec -T postgres psql -U "$(read_env DB_USER)" -d "$(read_env DB_NAME)" -Atc "SHOW max_connections;")"
(( pg_connections * 100 < pg_max * 85 )) || failures+=("PostgreSQL connections ${pg_connections}/${pg_max} exceed 85%")

redis_memory="$("${compose[@]}" exec -T redis redis-cli INFO memory | sed -n 's/^used_memory:\([0-9]*\).*/\1/p')"
redis_max="$("${compose[@]}" exec -T redis redis-cli CONFIG GET maxmemory | tail -n 1 | tr -d '\r')"
if [[ "$redis_memory" =~ ^[0-9]+$ && "$redis_max" =~ ^[0-9]+$ && "$redis_max" -gt 0 ]]; then
  (( redis_memory * 100 < redis_max * 85 )) || failures+=("Redis memory exceeds 85% of maxmemory")
fi
redis_rejected="$("${compose[@]}" exec -T redis redis-cli INFO stats | sed -n 's/^rejected_connections:\([0-9]*\).*/\1/p')"
[[ "${redis_rejected:-0}" == 0 ]] || warnings+=("Redis has ${redis_rejected} rejected connection(s) since startup")

nats_varz="$("${compose[@]}" exec -T nats wget -qO- http://127.0.0.1:8222/varz 2>/dev/null || true)"
nats_jsz="$("${compose[@]}" exec -T nats wget -qO- 'http://127.0.0.1:8222/jsz?streams=true&consumers=true' 2>/dev/null || true)"
if [[ -z "$nats_varz" || -z "$nats_jsz" ]]; then
  failures+=("NATS monitoring endpoints are unavailable")
else
  nats_check="$(python3 - "$nats_varz" "$nats_jsz" <<'PY_NATS'
import json,sys
varz=json.loads(sys.argv[1]); jsz=json.loads(sys.argv[2])
slow=int(varz.get('slow_consumers') or 0)
streams=int(jsz.get('streams') or 0)
consumers=int(jsz.get('consumers') or 0)
print(f'slow_consumers={slow} streams={streams} consumers={consumers}')
if slow or streams < 1 or consumers < 1: raise SystemExit(1)
PY_NATS
)" || failures+=("NATS/JetStream health is degraded: ${nats_check:-invalid response}")
fi

pgbouncer_waiting="$("${compose[@]}" exec -T pgbouncer sh -lc 'PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -p 6432 -U "$DB_USER" pgbouncer -Atc "SHOW POOLS"' 2>/dev/null | awk -F'|' '{s+=$4} END{print s+0}')"
(( ${pgbouncer_waiting:-0} < 5 )) || failures+=("PgBouncer has ${pgbouncer_waiting} waiting clients")

echo "Operational snapshot"
echo "  disk=${disk_percent}% available_memory=${available_mb}MB postgres_connections=${pg_connections}/${pg_max} pgbouncer_waiting=${pgbouncer_waiting:-unknown}"
[[ -f "$tmpdir/realtime-pipeline.json" ]] && echo "  realtime_pipeline=$(cat "$tmpdir/realtime-pipeline.json")"
[[ -f "$tmpdir/realtime-stats.json" ]] && echo "  axum=$(cat "$tmpdir/realtime-stats.json")"
if ((${#warnings[@]})); then printf 'WARNING: %s\n' "${warnings[@]}"; fi
if ((${#failures[@]})); then printf 'FAIL: %s\n' "${failures[@]}" >&2; exit 1; fi
echo "Operational health checks passed."
