#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


SERVICES = ("postgres", "redis", "web", "worker", "beat", "realtime", "frontend", "nginx")
PERFORMANCE_KEYS = (
    "CELERY_WORKER_CONCURRENCY",
    "CELERY_WORKER_PREFETCH_MULTIPLIER",
    "DATABASE_CONN_MAX_AGE",
    "DATABASE_CONN_HEALTH_CHECKS",
    "GUNICORN_THREADS",
    "GUNICORN_WORKERS",
    "REALTIME_HIGH_QUEUE_CAPACITY",
    "REALTIME_LOW_QUEUE_CAPACITY",
    "REALTIME_MAX_CONNECTION_AGE_SECONDS",
)
OBSERVED_ONLY_KEYS = ("REALTIME_MAX_CONNECTIONS",)
SOURCE_FILES = (
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.production.yml",
    "requirements.txt",
    "frontend/package-lock.json",
    "realtime/Cargo.lock",
    "realtime/Dockerfile",
)


def run(command: list[str], timeout: int = 15) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compose_base(env_path: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_path),
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.production.yml",
    ]


def build_payload(root: Path, env_path: Path) -> dict:
    env_values = parse_env(env_path)
    performance = {key: env_values.get(key, "") for key in PERFORMANCE_KEYS}
    observed_settings = {
        key: env_values.get(key, "")
        for key in (*PERFORMANCE_KEYS, *OBSERVED_ONLY_KEYS)
    }
    sources = {
        relative: file_sha256(root / relative)
        for relative in SOURCE_FILES
        if (root / relative).is_file()
    }

    images: dict[str, str] = {}
    compose = compose_base(env_path)
    for service in SERVICES:
        container_id = run([*compose, "ps", "-q", service])
        image_id = run(["docker", "inspect", "-f", "{{.Image}}", container_id]) if container_id else ""
        images[service] = image_id

    material = {
        "schema_version": 1,
        "performance_settings": performance,
        "container_image_ids": images,
        "source_sha256": sources,
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **material,
        "observed_performance_settings": observed_settings,
        "fingerprint": fingerprint,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--env-file", default=".env")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--hash", action="store_true")
    output_group.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = root / env_path
    if not env_path.exists():
        parser.error(f"Missing environment file: {env_path}")

    payload = build_payload(root, env_path)
    if args.hash:
        print(payload["fingerprint"])
    else:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
