#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

backup="${1:-}"
confirmation="${2:-}"
[[ -n "$backup" ]] || { echo "Usage: $0 backups/messenger-media-*.tar.gz --confirm" >&2; exit 2; }
[[ "$confirmation" == "--confirm" ]] || { echo "Restore is destructive. Re-run with --confirm." >&2; exit 2; }
[[ -f "$backup" ]] || { echo "Backup not found: $backup" >&2; exit 1; }
[[ -f .env ]] || { echo "Missing .env." >&2; exit 1; }

if [[ -f "${backup}.sha256" ]]; then
  sha256sum -c "${backup}.sha256"
fi
python3 - "$backup" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        name = member.name
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit(f"Unsafe archive member: {name}")
print("Media archive validation passed.")
PY

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
restart_services=0
cleanup() {
  if (( restart_services )); then
    "${compose[@]}" start web worker beat nginx >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Preserve the current media volume before replacing it.
./scripts/backup-media.sh
"${compose[@]}" stop web worker beat nginx
restart_services=1

cat "$backup" | "${compose[@]}" run --rm --no-deps -T --user root \
  -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 web python -c '
import shutil, sys, tarfile
from pathlib import Path
root = Path("/app/media")
root.mkdir(parents=True, exist_ok=True)
for child in root.iterdir():
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child)
    else:
        child.unlink()
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
    archive.extractall(root, filter="data")
'

"${compose[@]}" start web worker beat nginx
restart_services=0
"${compose[@]}" exec -T web python manage.py check

echo "Media restore completed from $backup"
