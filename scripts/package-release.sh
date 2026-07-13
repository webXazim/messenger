#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

output="${1:-../messenger-release.zip}"
python3 - "$output" <<'PY'
from pathlib import Path
import sys, zipfile

root = Path.cwd()
output = Path(sys.argv[1]).expanduser().resolve()
excluded_dirs = {
    ".git", ".idea", ".vscode", "node_modules", "dist", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "media", "private_media", "backups",
}
excluded_names = {".env", "db.sqlite3", ".DS_Store"}
excluded_suffixes = {".pyc", ".pyo", ".zip", ".log"}

def include(path: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in excluded_dirs for part in rel.parts):
        return False
    if path.name in excluded_names or path.suffix in excluded_suffixes:
        return False
    if rel.parts and rel.parts[0] == "secrets" and path.name not in {".gitkeep", "README.txt"}:
        return False
    return path.is_file()

output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(root.rglob("*")):
        if include(path):
            archive.write(path, Path("messenger") / path.relative_to(root))

with zipfile.ZipFile(output) as archive:
    names = archive.namelist()
    forbidden = [name for name in names if name.endswith("/.env") or "/node_modules/" in name or "/secrets/" in name and not name.endswith(("/.gitkeep", "/README.txt"))]
    if forbidden:
        raise SystemExit(f"Unsafe release entries: {forbidden[:5]}")
print(f"Created safe release archive: {output}")
PY
