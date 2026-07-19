#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from deployment_fingerprint import build_payload


ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "scripts" / "analyze-load-test.py"


def write(path: Path, payload) -> None:
    if isinstance(payload, list):
        path.write_text("\n".join(json.dumps(item) for item in payload) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def capacity_summary() -> dict:
    return {
        "metrics": {
            "checks": {"values": {"rate": 1.0}},
            "realtime_ws_failures": {"values": {"rate": 0.0}},
            "http_req_failed": {"values": {"rate": 0.0}},
            "ws_connecting": {"values": {"p(95)": 200.0}},
            "realtime_control_latency": {"values": {"p(95)": 20.0}},
        }
    }


def mixed_summary() -> dict:
    return {
        "metrics": {
            "checks": {"values": {"rate": 1.0}},
            "mixed_ws_failures": {"values": {"rate": 0.0}},
            "mixed_read_failures": {"values": {"rate": 0.0}},
            "mixed_write_failures": {"values": {"rate": 0.0}},
            "http_req_failed": {"values": {"rate": 0.0}},
            "mixed_conversation_read_latency": {"values": {"p(95)": 80.0}},
            "mixed_message_read_latency": {"values": {"p(95)": 90.0}},
            "mixed_message_write_latency": {"values": {"p(95)": 100.0}},
            "mixed_control_latency": {"values": {"p(95)": 25.0}},
            "mixed_events_received": {"values": {"count": 10.0}},
            "dropped_iterations": {"values": {"count": 0.0}},
        }
    }


def record(*, fingerprint: str, connections: int, counters: int) -> dict:
    containers = {
        service: {
            "missing": False,
            "status": "running",
            "restarts": 0,
            "cpu_percent": 5.0,
            "memory_percent": 20.0,
        }
        for service in ("postgres", "redis", "web", "worker", "beat", "realtime", "frontend", "nginx")
    }
    return {
        "deployment": {"fingerprint": fingerprint, "observed_performance_settings": {"REALTIME_MAX_CONNECTIONS": "100"}},
        "host": {"available_memory_mb": 600, "disk_percent": 20, "cpu_count": 2},
        "containers": containers,
        "postgres": {
            "connections": 8,
            "max_connections": 40,
            "xact_commit": 1000 + counters,
            "xact_rollback": 1,
            "deadlocks": 0,
            "temp_files": 0,
        },
        "redis": {
            "used_memory": 20,
            "maxmemory": 100,
            "rejected_connections": 0,
            "evicted_keys": 0,
        },
        "axum": {
            "connections": connections,
            "connections_rejected": 0,
            "disconnected_slow": 0,
            "stream_errors": 0,
            "malformed_stream_events": 0,
            "stream_ready": True,
        },
        "pipeline": {"ok": True, "consumer_pending": 0, "outbox_failed": 0},
    }


def run_case(directory: Path, *, second_fingerprint: str) -> tuple[int, dict]:
    capacity = directory / "capacity.json"
    mixed = directory / "mixed.json"
    metrics = directory / "metrics.jsonl"
    audit = directory / "audit.json"
    plans = directory / "plans.json"
    output = directory / "report.json"
    write(capacity, capacity_summary())
    write(mixed, mixed_summary())
    write(metrics, [record(fingerprint="release-a", connections=0, counters=0), record(fingerprint=second_fingerprint, connections=100, counters=100)])
    write(audit, {"passed": True, "warnings": []})
    write(plans, {
        "strict_passed": True,
        "analyzed": True,
        "warnings": [],
        "skipped": [],
        "plans": [
            {"name": name, "analyzed": True, "plan_fingerprint": name}
            for name in ("a", "b", "c", "d", "e")
        ],
    })
    completed = subprocess.run(
        [
            sys.executable,
            str(ANALYZER),
            "--summary", str(capacity),
            "--mixed-summary", str(mixed),
            "--server-metrics", str(metrics),
            "--postgres-audit", str(audit),
            "--query-plans", str(plans),
            "--target-connections", "100",
            "--output", str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, json.loads(output.read_text())


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        returncode, report = run_case(directory, second_fingerprint="release-a")
        if returncode != 0 or report.get("passed") is not True:
            raise SystemExit("Synthetic passing capacity report did not pass.")
        if report.get("recommended_production_max_connections") != 75:
            raise SystemExit("The 20% headroom recommendation was not rounded safely.")

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        returncode, report = run_case(directory, second_fingerprint="release-b")
        if returncode == 0 or report.get("passed") is not False:
            raise SystemExit("Deployment fingerprint drift was not rejected.")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        env = root / ".env"
        env.write_text("REALTIME_MAX_CONNECTIONS=500\nGUNICORN_WORKERS=2\n", encoding="utf-8")
        first = build_payload(root, env)["fingerprint"]
        env.write_text("REALTIME_MAX_CONNECTIONS=650\nGUNICORN_WORKERS=2\n", encoding="utf-8")
        second = build_payload(root, env)["fingerprint"]
        if first != second:
            raise SystemExit("The temporary admission ceiling incorrectly invalidates the deployment fingerprint.")
        env.write_text("REALTIME_MAX_CONNECTIONS=650\nGUNICORN_WORKERS=3\n", encoding="utf-8")
        third = build_payload(root, env)["fingerprint"]
        if third == second:
            raise SystemExit("A fixed worker-setting change did not invalidate the deployment fingerprint.")

    print("Capacity analyzer and deployment fingerprint synthetic tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
