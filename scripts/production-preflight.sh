#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

fail() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
[ -f .env ] || fail "Create .env from .env.production.example or .env.example"
[ -s secrets/realtime-private.pem ] || fail "Missing secrets/realtime-private.pem"
[ -s secrets/realtime-public.pem ] || fail "Missing secrets/realtime-public.pem"

info "Generating the locked Rust dependency graph"
./scripts/generate-realtime-lockfile.sh

info "Validating Compose configuration"
docker compose config --quiet

info "Building application images"
docker compose build --pull web worker beat realtime frontend pgbouncer

info "Starting infrastructure"
docker compose up -d postgres redis pgbouncer nats

info "Waiting for infrastructure health"
for service in postgres redis pgbouncer nats; do
  attempts=0
  while :; do
    cid=$(docker compose ps -q "$service")
    [ -n "$cid" ] || fail "$service container was not created"
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")
    [ "$status" = healthy ] && break
    [ "$status" = exited ] || [ "$status" = dead ] && fail "$service stopped during startup"
    attempts=$((attempts + 1))
    [ "$attempts" -lt 40 ] || fail "$service did not become healthy"
    sleep 3
  done
done

info "Running Django deployment checks"
docker compose run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 web python manage.py check --deploy

info "Checking unapplied migrations"
docker compose run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 web python manage.py makemigrations --check --dry-run

info "Applying migrations through direct PostgreSQL"
docker compose run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 \
  -e DATABASE_RUNTIME_ENDPOINT=postgres web python manage.py migrate --noinput

info "Initializing NATS JetStream"
docker compose run --rm -e RUN_MIGRATIONS=0 -e RUN_COLLECTSTATIC=0 -e ENSURE_NATS_STREAM=0 \
  web python manage.py ensure_nats_stream

info "Starting application services"
docker compose up -d web worker beat realtime frontend nginx

info "Waiting for application health"
for service in web realtime frontend nginx; do
  attempts=0
  while :; do
    cid=$(docker compose ps -q "$service")
    [ -n "$cid" ] || fail "$service container was not created"
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")
    [ "$status" = healthy ] && break
    [ "$status" = exited ] || [ "$status" = dead ] && {
      docker compose logs --tail=150 "$service" >&2 || true
      fail "$service stopped during startup"
    }
    attempts=$((attempts + 1))
    [ "$attempts" -lt 60 ] || {
      docker compose logs --tail=150 "$service" >&2 || true
      fail "$service did not become healthy"
    }
    sleep 3
  done
done

info "Running runtime smoke checks"
docker compose exec -T web python manage.py check
curl -fsS http://127.0.0.1:8080/api/v1/health/live/ >/dev/null 2>&1 || \
  echo "NOTE: Host port 8080 is not published by the selected compose override; container health still passed."

info "Production preflight completed successfully"
docker compose ps
