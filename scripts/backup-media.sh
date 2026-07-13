#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required." >&2; exit 1; }
mkdir -p backups

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="backups/messenger-media-${stamp}.tar.gz"
tmp="${output}.partial"
compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
trap 'rm -f "$tmp"' EXIT

"${compose[@]}" exec -T web python -c '
import sys, tarfile
from pathlib import Path
root = Path("/app/media")
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    if root.exists():
        for path in sorted(root.rglob("*")):
            archive.add(path, arcname=path.relative_to(root), recursive=False)
' > "$tmp"

python3 - "$tmp" <<'PY'
import sys, tarfile
path = sys.argv[1]
with tarfile.open(path, "r:gz") as archive:
    for member in archive.getmembers():
        name = member.name
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit(f"Unsafe archive member: {name}")
print("Media archive validation passed.")
PY

mv "$tmp" "$output"
trap - EXIT
sha256sum "$output" > "${output}.sha256"
chmod 0600 "$output" "${output}.sha256"
echo "Created and verified $output"
echo "This contains public profile photos. Copy it and the checksum to encrypted off-server storage."
