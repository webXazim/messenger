#!/usr/bin/env sh
set -eu

COMPOSE=${COMPOSE:-"docker compose"}

$COMPOSE ps realtime
$COMPOSE exec -T realtime curl -fsS http://127.0.0.1:9000/health/live
printf '\n'
$COMPOSE exec -T realtime curl -fsS http://127.0.0.1:9000/health/ready
printf '\n'
$COMPOSE exec -T realtime curl -fsS http://127.0.0.1:9000/internal/stats
printf '\n'
