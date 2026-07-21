#!/usr/bin/env python3
"""Atomically update selected keys in a dotenv file without exposing secrets."""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def parse_assignments(items: list[str]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid assignment {item!r}; expected KEY=VALUE")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key or any(ch.isspace() for ch in key):
            raise SystemExit(f"Invalid environment key {key!r}")
        updates[key] = value
    return updates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("assignments", nargs="+")
    parser.add_argument("--backup-dir", type=Path, default=Path(".deployment-state"))
    parser.add_argument("--label", default="env-update")
    args = parser.parse_args()

    env_file = args.env_file
    if not env_file.is_file():
        raise SystemExit(f"Environment file does not exist: {env_file}")

    updates = parse_assignments(args.assignments)
    raw = env_file.read_text(encoding="utf-8")
    lines = raw.splitlines()
    seen: set[str] = set()
    output: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)

    missing = [key for key in updates if key not in seen]
    if missing:
        output.append("")
        output.append("# Added by controlled architecture activation")
        output.extend(f"{key}={updates[key]}" for key in missing)

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = args.backup_dir / f"{args.label}-{timestamp}.env"
    shutil.copy2(env_file, backup)

    fd, temp_name = tempfile.mkstemp(prefix=f".{env_file.name}.", dir=str(env_file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, env_file.stat().st_mode)
        os.replace(temp_name, env_file)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    latest = args.backup_dir / "latest-env-backup.env"
    shutil.copy2(backup, latest)
    print(backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
