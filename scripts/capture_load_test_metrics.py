#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from deployment_fingerprint import build_payload as build_deployment_fingerprint

STOP = False


def stop(*_args):
    global STOP
    STOP = True


def run(command: list[str], timeout: int = 12) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def compose_base() -> list[str]:
    return [
        "docker", "compose", "--env-file", ".env",
        "-f", "docker-compose.yml", "-f", "docker-compose.production.yml",
    ]


def service_container(service: str) -> str:
    return run([*compose_base(), "ps", "-q", service])


def parse_percent(value: str) -> float:
    try:
        return float(value.strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.0


def parse_bytes(value: str) -> int:
    match = re.match(r"^\s*([0-9.]+)\s*([KMGTP]?i?B)?\s*$", value or "", re.I)
    if not match:
        return 0
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    multipliers = {
        "B": 1, "KB": 1000, "KIB": 1024,
        "MB": 1000**2, "MIB": 1024**2,
        "GB": 1000**3, "GIB": 1024**3,
        "TB": 1000**4, "TIB": 1024**4,
    }
    return int(number * multipliers.get(unit, 1))


def container_snapshot(service: str) -> dict:
    container_id = service_container(service)
    if not container_id:
        return {"missing": True}
    raw = run([
        "docker", "stats", "--no-stream",
        "--format", "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.PIDs}}",
        container_id,
    ])
    values = raw.split("|", 3) if raw else []
    state = run(["docker", "inspect", "-f", "{{.State.Status}}|{{.RestartCount}}", container_id])
    state_values = state.split("|", 1) if state else []
    usage = values[1].split("/", 1)[0].strip() if len(values) > 1 else ""
    return {
        "missing": False,
        "status": state_values[0] if state_values else "unknown",
        "restarts": int(state_values[1]) if len(state_values) > 1 and state_values[1].isdigit() else 0,
        "cpu_percent": parse_percent(values[0]) if values else 0.0,
        "memory_usage_bytes": parse_bytes(usage),
        "memory_percent": parse_percent(values[2]) if len(values) > 2 else 0.0,
        "pids": int(values[3]) if len(values) > 3 and values[3].isdigit() else 0,
    }


def json_command(command: list[str]) -> dict:
    raw = run(command)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:1000]}


def host_snapshot() -> dict:
    mem_available_kb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])
                break
    except (OSError, ValueError):
        pass
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    disk = os.statvfs(".")
    used = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
    total = disk.f_blocks * disk.f_frsize
    return {
        "available_memory_mb": round(mem_available_kb / 1024, 2),
        "load_1": round(load1, 3),
        "load_5": round(load5, 3),
        "load_15": round(load15, 3),
        "disk_percent": round((used / total * 100) if total else 0.0, 2),
        "cpu_count": int(os.cpu_count() or 1),
    }


def database_snapshot() -> dict:
    compose = compose_base()
    db_user = os.getenv("DB_USER", "")
    db_name = os.getenv("DB_NAME", "")
    if not db_user or not db_name:
        env_text = Path(".env").read_text(encoding="utf-8")
        values = {}
        for line in env_text.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
        db_user = values.get("DB_USER", "")
        db_name = values.get("DB_NAME", "")
    query = """
    SELECT
      (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()),
      current_setting('max_connections')::bigint,
      COALESCE(xact_commit, 0),
      COALESCE(xact_rollback, 0),
      COALESCE(deadlocks, 0),
      COALESCE(temp_files, 0),
      COALESCE(temp_bytes, 0),
      COALESCE(blk_read_time, 0),
      COALESCE(blk_write_time, 0)
    FROM pg_stat_database
    WHERE datname = current_database();
    """
    raw = run([*compose, "exec", "-T", "postgres", "psql", "-U", db_user, "-d", db_name, "-At", "-F", "|", "-c", query])
    parts = raw.split("|") if raw else []

    def number(index: int, *, floating: bool = False):
        try:
            return float(parts[index]) if floating else int(float(parts[index]))
        except (IndexError, TypeError, ValueError):
            return 0.0 if floating else 0

    return {
        "connections": number(0),
        "max_connections": number(1),
        "xact_commit": number(2),
        "xact_rollback": number(3),
        "deadlocks": number(4),
        "temp_files": number(5),
        "temp_bytes": number(6),
        "block_read_time_ms": number(7, floating=True),
        "block_write_time_ms": number(8, floating=True),
    }


def redis_snapshot() -> dict:
    compose = compose_base()
    memory = run([*compose, "exec", "-T", "redis", "redis-cli", "INFO", "memory"])
    stats = run([*compose, "exec", "-T", "redis", "redis-cli", "INFO", "stats"])
    config = run([*compose, "exec", "-T", "redis", "redis-cli", "CONFIG", "GET", "maxmemory"])
    def field(text: str, name: str) -> int:
        match = re.search(rf"^{re.escape(name)}:([0-9]+)", text, re.M)
        return int(match.group(1)) if match else 0
    max_lines = [line.strip() for line in config.splitlines() if line.strip()]
    maxmemory = int(max_lines[-1]) if max_lines and max_lines[-1].isdigit() else 0
    hits = field(stats, "keyspace_hits")
    misses = field(stats, "keyspace_misses")
    return {
        "used_memory": field(memory, "used_memory"),
        "maxmemory": maxmemory,
        "rejected_connections": field(stats, "rejected_connections"),
        "evicted_keys": field(stats, "evicted_keys"),
        "total_commands_processed": field(stats, "total_commands_processed"),
        "instantaneous_ops_per_sec": field(stats, "instantaneous_ops_per_sec"),
        "keyspace_hits": hits,
        "keyspace_misses": misses,
        "keyspace_hit_ratio": round(hits / max(1, hits + misses), 4),
    }


def sample(deployment: dict) -> dict:
    compose = compose_base()
    containers = {service: container_snapshot(service) for service in (
        "postgres", "redis", "web", "worker", "beat", "realtime", "frontend", "nginx"
    )}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "deployment": deployment,
        "host": host_snapshot(),
        "containers": containers,
        "postgres": database_snapshot(),
        "redis": redis_snapshot(),
        "axum": json_command([*compose, "exec", "-T", "realtime", "curl", "-fsS", "http://127.0.0.1:9000/internal/stats"]),
        "pipeline": json_command([*compose, "exec", "-T", "web", "python", "manage.py", "check_realtime_pipeline", "--json", "--warn-only"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--output", default="loadtests/results/vps-metrics.jsonl")
    args = parser.parse_args()
    if not Path(".env").exists():
        parser.error("Run from the project directory containing .env.")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    deployment = build_deployment_fingerprint(Path.cwd(), Path(".env").resolve())
    deadline = time.monotonic() + max(5, args.duration)
    with output.open("a", encoding="utf-8") as handle:
        while not STOP and time.monotonic() < deadline:
            started = time.monotonic()
            record = sample(deployment)
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
            print(
                f"{record['timestamp']} connections={record['axum'].get('connections', '?')} "
                f"available_mb={record['host']['available_memory_mb']}"
            )
            sleep_for = max(0.0, args.interval - (time.monotonic() - started))
            time.sleep(sleep_for)
    print(f"VPS metrics written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
