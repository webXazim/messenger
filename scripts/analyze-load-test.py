#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metric(summary: dict, name: str, key: str, default=0.0) -> float:
    values = summary.get("metrics", {}).get(name, {}).get("values", {})
    value = values.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile_value / 100 * len(ordered)) - 1))
    return ordered[index]


def read_server_metrics(path: str) -> list[dict]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def delta(records: list[dict], section: str, key: str) -> float:
    first = float(records[0].get(section, {}).get(key, 0) or 0)
    last = float(records[-1].get(section, {}).get(key, 0) or 0)
    return max(0.0, last - first)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True, help="k6 realtime-capacity summary JSON.")
    parser.add_argument("--mixed-summary", required=True, help="k6 mixed-production summary JSON.")
    parser.add_argument("--server-metrics", required=True)
    parser.add_argument("--postgres-audit", required=True)
    parser.add_argument("--query-plans", required=True)
    parser.add_argument("--target-connections", required=True, type=int)
    parser.add_argument("--mixed-read-rps", type=int, default=20)
    parser.add_argument("--mixed-write-rps", type=int, default=5)
    parser.add_argument("--output", default="loadtests/results/capacity-report.json")
    parser.add_argument("--min-memory-mb", type=int, default=256)
    parser.add_argument("--valid-hours", type=int, default=168)
    args = parser.parse_args()

    capacity_summary = load_json(args.summary)
    mixed_summary = load_json(args.mixed_summary)
    postgres_audit = load_json(args.postgres_audit)
    query_plans = load_json(args.query_plans)
    records = read_server_metrics(args.server_metrics)
    if not records:
        parser.error("The server metrics file contains no valid samples.")

    capacity_checks_rate = metric(capacity_summary, "checks", "rate", 1.0)
    capacity_failure_rate = max(
        metric(capacity_summary, "realtime_ws_failures", "rate", 0.0),
        metric(capacity_summary, "http_req_failed", "rate", 0.0),
    )
    ws_p95 = metric(capacity_summary, "ws_connecting", "p(95)", 0.0)
    control_p95 = metric(capacity_summary, "realtime_control_latency", "p(95)", 0.0)

    mixed_checks_rate = metric(mixed_summary, "checks", "rate", 1.0)
    mixed_failure_rate = max(
        metric(mixed_summary, "mixed_ws_failures", "rate", 0.0),
        metric(mixed_summary, "mixed_read_failures", "rate", 0.0),
        metric(mixed_summary, "mixed_write_failures", "rate", 0.0),
        metric(mixed_summary, "http_req_failed", "rate", 0.0),
    )
    mixed_conversation_p95 = metric(mixed_summary, "mixed_conversation_read_latency", "p(95)", 0.0)
    mixed_message_read_p95 = metric(mixed_summary, "mixed_message_read_latency", "p(95)", 0.0)
    mixed_message_write_p95 = metric(mixed_summary, "mixed_message_write_latency", "p(95)", 0.0)
    mixed_control_p95 = metric(mixed_summary, "mixed_control_latency", "p(95)", 0.0)
    mixed_events = metric(mixed_summary, "mixed_events_received", "count", 0.0)
    mixed_dropped_iterations = metric(mixed_summary, "dropped_iterations", "count", 0.0)

    available_memory = [float(item.get("host", {}).get("available_memory_mb", 0)) for item in records]
    disk = [float(item.get("host", {}).get("disk_percent", 0)) for item in records]
    cpu_count = max(1, int(records[0].get("host", {}).get("cpu_count", 1) or 1))
    total_cpu = [
        sum(float(service.get("cpu_percent", 0)) for service in item.get("containers", {}).values())
        for item in records
    ]
    cpu_p95 = percentile(total_cpu, 95)
    cpu_threshold = cpu_count * 80.0
    max_memory_percent = max(
        (float(service.get("memory_percent", 0)) for item in records for service in item.get("containers", {}).values()),
        default=0.0,
    )
    first_containers = records[0].get("containers", {})
    last_containers = records[-1].get("containers", {})
    restart_delta = max(
        (
            int(last_containers.get(service, {}).get("restarts", 0))
            - int(first_containers.get(service, {}).get("restarts", 0))
            for service in set(first_containers) | set(last_containers)
        ),
        default=0,
    )
    max_connections = max((int(item.get("axum", {}).get("connections", 0)) for item in records), default=0)
    rejected_delta = int(delta(records, "axum", "connections_rejected"))
    slow_delta = int(delta(records, "axum", "disconnected_slow"))
    stream_errors_delta = int(delta(records, "axum", "stream_errors"))
    malformed_stream_delta = int(delta(records, "axum", "malformed_stream_events"))
    max_pg_ratio = max(
        (
            float(item.get("postgres", {}).get("connections", 0))
            / max(1, float(item.get("postgres", {}).get("max_connections", 0)))
            for item in records
        ),
        default=0.0,
    )
    max_redis_ratio = max(
        (
            float(item.get("redis", {}).get("used_memory", 0))
            / max(1, float(item.get("redis", {}).get("maxmemory", 0)))
            for item in records
            if float(item.get("redis", {}).get("maxmemory", 0)) > 0
        ),
        default=0.0,
    )
    max_pending = max((int(item.get("pipeline", {}).get("outbox_pending", item.get("pipeline", {}).get("consumer_pending", 0)) or 0) for item in records), default=0)
    max_failed_outbox = max((int(item.get("pipeline", {}).get("outbox_failed", 0) or 0) for item in records), default=0)
    pipeline_healthy = all(item.get("pipeline", {}).get("ok") is True for item in records)
    axum_ready = all(
        item.get("axum", {}).get("stream_ready") is True
        and item.get("axum", {}).get("ephemeral_ready") is True
        and item.get("axum", {}).get("ownership_ready") is True
        for item in records
    )
    nats_slow_delta = int(delta(records, "nats", "slow_consumers"))
    nats_connections_max = max((int(item.get("nats", {}).get("connections", 0) or 0) for item in records), default=0)
    nats_js_storage_max = max((int(item.get("nats", {}).get("jetstream", {}).get("storage", 0) or 0) for item in records), default=0)
    nats_js_memory_max = max((int(item.get("nats", {}).get("jetstream", {}).get("memory", 0) or 0) for item in records), default=0)
    pgbouncer_waiting_max = max((int(item.get("pgbouncer", {}).get("client_waiting", 0) or 0) for item in records), default=0)
    redis_rejected_delta = int(delta(records, "redis", "rejected_connections"))
    redis_evicted_delta = int(delta(records, "redis", "evicted_keys"))
    postgres_deadlocks_delta = int(delta(records, "postgres", "deadlocks"))
    postgres_temp_files_delta = int(delta(records, "postgres", "temp_files"))
    postgres_commits_delta = delta(records, "postgres", "xact_commit")
    postgres_rollbacks_delta = delta(records, "postgres", "xact_rollback")
    postgres_rollback_ratio = postgres_rollbacks_delta / max(1.0, postgres_commits_delta + postgres_rollbacks_delta)
    containers_healthy = all(
        not service.get("missing", False) and service.get("status") == "running"
        for item in records for service in item.get("containers", {}).values()
    )

    deployment_fingerprints = {
        str(item.get("deployment", {}).get("fingerprint", ""))
        for item in records
        if item.get("deployment", {}).get("fingerprint")
    }
    deployment_fingerprint = next(iter(deployment_fingerprints), "") if len(deployment_fingerprints) == 1 else ""
    observed_admission_limits = {
        int(value)
        for item in records
        for value in [item.get("deployment", {}).get("observed_performance_settings", {}).get("REALTIME_MAX_CONNECTIONS", "")]
        if str(value).isdigit()
    }
    observed_admission_limit = next(iter(observed_admission_limits), 0) if len(observed_admission_limits) == 1 else 0

    plans_complete = not query_plans.get("skipped") and len(query_plans.get("plans", [])) >= 5
    plans_analyzed = query_plans.get("analyzed") is True and all(
        plan.get("analyzed") is True for plan in query_plans.get("plans", [])
    )

    criteria = {
        "capacity_checks_rate_at_least_99_percent": capacity_checks_rate >= 0.99,
        "capacity_failure_rate_below_1_percent": capacity_failure_rate < 0.01,
        "websocket_connect_p95_below_1500ms": ws_p95 == 0 or ws_p95 < 1500,
        "control_latency_p95_below_300ms": control_p95 == 0 or control_p95 < 300,
        "target_connections_reached": max_connections >= math.floor(args.target_connections * 0.95),
        "mixed_checks_rate_at_least_99_percent": mixed_checks_rate >= 0.99,
        "mixed_failure_rate_below_1_percent": mixed_failure_rate < 0.01,
        "mixed_conversation_read_p95_below_500ms": 0 < mixed_conversation_p95 < 500,
        "mixed_message_read_p95_below_500ms": 0 < mixed_message_read_p95 < 500,
        "mixed_message_write_p95_below_500ms": 0 < mixed_message_write_p95 < 500,
        "mixed_control_latency_p95_below_350ms": mixed_control_p95 == 0 or mixed_control_p95 < 350,
        "mixed_realtime_events_observed": mixed_events > 0,
        "mixed_no_dropped_iterations": mixed_dropped_iterations < 1,
        "available_memory_at_least_threshold": min(available_memory) >= args.min_memory_mb,
        "container_memory_below_85_percent": max_memory_percent < 85,
        "total_cpu_p95_below_80_percent_of_host": cpu_p95 < cpu_threshold,
        "postgres_connections_below_85_percent": max_pg_ratio < 0.85,
        "postgres_rollback_ratio_below_5_percent": postgres_rollback_ratio < 0.05,
        "postgres_no_deadlocks": postgres_deadlocks_delta == 0,
        "postgres_temp_files_below_5": postgres_temp_files_delta < 5,
        "redis_memory_below_85_percent": max_redis_ratio < 0.85,
        "redis_no_evictions": redis_evicted_delta == 0,
        "no_connection_rejections": rejected_delta == 0,
        "slow_disconnect_rate_below_1_percent": slow_delta < max(1, args.target_connections * 0.01),
        "no_axum_stream_errors": stream_errors_delta == 0,
        "no_malformed_stream_events": malformed_stream_delta == 0,
        "no_container_restarts": restart_delta == 0,
        "durable_outbox_pending_below_250": max_pending < 250,
        "failed_outbox_below_25": max_failed_outbox < 25,
        "pipeline_healthy_for_all_samples": pipeline_healthy,
        "axum_nats_transports_ready_for_all_samples": axum_ready,
        "nats_no_new_slow_consumers": nats_slow_delta == 0,
        "nats_has_application_connections": nats_connections_max >= 2,
        "nats_jetstream_storage_below_1536mb": nats_js_storage_max < 1536 * 1024 * 1024,
        "nats_jetstream_memory_below_28mb": nats_js_memory_max < 28 * 1024 * 1024,
        "pgbouncer_waiting_clients_below_5": pgbouncer_waiting_max < 5,
        "redis_rejected_connections_unchanged": redis_rejected_delta == 0,
        "all_required_containers_running": containers_healthy,
        "disk_below_85_percent": max(disk) < 85,
        "single_deployment_fingerprint_for_all_samples": bool(deployment_fingerprint),
        "single_test_admission_limit_for_all_samples": bool(observed_admission_limit),
        "test_admission_limit_at_least_target": observed_admission_limit >= args.target_connections,
        "postgres_audit_passed": postgres_audit.get("passed") is True,
        "critical_query_plans_passed_strictly": query_plans.get("strict_passed") is True,
        "critical_query_plan_coverage_complete": plans_complete,
        "critical_query_plans_used_explain_analyze": plans_analyzed,
    }
    passed = all(criteria.values())
    recommendation = int(math.floor(args.target_connections * 0.80 / 25) * 25) if passed else 0
    generated_at = datetime.now(timezone.utc)
    valid_until = generated_at + timedelta(hours=max(1, int(args.valid_hours)))

    report = {
        "schema_version": 3,
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "passed": passed,
        "verification_complete": True,
        "deployment_fingerprint": deployment_fingerprint,
        "tested_connections": args.target_connections,
        "tested_mixed_read_rps": args.mixed_read_rps,
        "tested_mixed_write_rps": args.mixed_write_rps,
        "recommended_production_max_connections": recommendation,
        "headroom_policy": "80_percent_of_verified_peak_rounded_down_to_25",
        "criteria": criteria,
        "observations": {
            "capacity_checks_rate": capacity_checks_rate,
            "capacity_failure_rate": capacity_failure_rate,
            "ws_connecting_p95_ms": ws_p95,
            "control_latency_p95_ms": control_p95,
            "mixed_checks_rate": mixed_checks_rate,
            "mixed_failure_rate": mixed_failure_rate,
            "mixed_conversation_read_p95_ms": mixed_conversation_p95,
            "mixed_message_read_p95_ms": mixed_message_read_p95,
            "mixed_message_write_p95_ms": mixed_message_write_p95,
            "mixed_control_latency_p95_ms": mixed_control_p95,
            "mixed_events_received": mixed_events,
            "mixed_dropped_iterations": mixed_dropped_iterations,
            "max_observed_connections": max_connections,
            "observed_test_admission_limit": observed_admission_limit,
            "minimum_available_memory_mb": min(available_memory),
            "container_memory_max_percent": max_memory_percent,
            "host_cpu_count": cpu_count,
            "total_container_cpu_p95_percent": cpu_p95,
            "total_container_cpu_threshold_percent": cpu_threshold,
            "postgres_connection_max_ratio": max_pg_ratio,
            "postgres_commit_delta": postgres_commits_delta,
            "postgres_rollback_delta": postgres_rollbacks_delta,
            "postgres_rollback_ratio": postgres_rollback_ratio,
            "postgres_deadlocks_delta": postgres_deadlocks_delta,
            "postgres_temp_files_delta": postgres_temp_files_delta,
            "redis_memory_max_ratio": max_redis_ratio,
            "redis_evicted_keys_delta": redis_evicted_delta,
            "connection_rejections_delta": rejected_delta,
            "slow_disconnects_delta": slow_delta,
            "axum_stream_errors_delta": stream_errors_delta,
            "malformed_stream_events_delta": malformed_stream_delta,
            "container_restart_delta_max": restart_delta,
            "durable_outbox_pending_max": max_pending,
            "nats_slow_consumers_delta": nats_slow_delta,
            "nats_connections_max": nats_connections_max,
            "nats_jetstream_storage_max_bytes": nats_js_storage_max,
            "nats_jetstream_memory_max_bytes": nats_js_memory_max,
            "pgbouncer_waiting_clients_max": pgbouncer_waiting_max,
            "failed_outbox_max": max_failed_outbox,
            "redis_rejected_connections_delta": redis_rejected_delta,
            "disk_max_percent": max(disk),
            "sample_count": len(records),
        },
        "diagnostics": {
            "postgres_audit_warnings": postgres_audit.get("warnings", []),
            "query_plan_warnings": query_plans.get("warnings", []),
            "query_plan_fingerprints": {
                plan.get("name", "unknown"): plan.get("plan_fingerprint", "")
                for plan in query_plans.get("plans", [])
            },
        },
        "source_sha256": {
            "capacity_summary": file_sha256(args.summary),
            "mixed_summary": file_sha256(args.mixed_summary),
            "server_metrics": file_sha256(args.server_metrics),
            "postgres_audit": file_sha256(args.postgres_audit),
            "query_plans": file_sha256(args.query_plans),
        },
    }
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("PASS" if passed else "FAIL", f"tested={args.target_connections}", f"recommended={recommendation}")
    print(f"Deployment fingerprint: {deployment_fingerprint or 'missing'}")
    for name, value in criteria.items():
        print(f"  {'PASS' if value else 'FAIL'} {name}")
    print(f"Report: {output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
