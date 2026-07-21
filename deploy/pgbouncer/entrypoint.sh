#!/bin/sh
set -eu

: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"

DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
PGBOUNCER_LISTEN_ADDR="${PGBOUNCER_LISTEN_ADDR:-0.0.0.0}"
PGBOUNCER_LISTEN_PORT="${PGBOUNCER_LISTEN_PORT:-6432}"
PGBOUNCER_POOL_MODE="${PGBOUNCER_POOL_MODE:-transaction}"
PGBOUNCER_DEFAULT_POOL_SIZE="${PGBOUNCER_DEFAULT_POOL_SIZE:-10}"
PGBOUNCER_RESERVE_POOL_SIZE="${PGBOUNCER_RESERVE_POOL_SIZE:-2}"
PGBOUNCER_MAX_CLIENT_CONN="${PGBOUNCER_MAX_CLIENT_CONN:-100}"
PGBOUNCER_SERVER_IDLE_TIMEOUT="${PGBOUNCER_SERVER_IDLE_TIMEOUT:-60}"
PGBOUNCER_QUERY_TIMEOUT="${PGBOUNCER_QUERY_TIMEOUT:-30}"
PGBOUNCER_CLIENT_IDLE_TIMEOUT="${PGBOUNCER_CLIENT_IDLE_TIMEOUT:-300}"

cat > /etc/pgbouncer/pgbouncer.ini <<CFG
[databases]
${DB_NAME} = host=${DB_HOST} port=${DB_PORT} dbname=${DB_NAME}

[pgbouncer]
listen_addr = ${PGBOUNCER_LISTEN_ADDR}
listen_port = ${PGBOUNCER_LISTEN_PORT}
auth_type = plain
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = ${PGBOUNCER_POOL_MODE}
default_pool_size = ${PGBOUNCER_DEFAULT_POOL_SIZE}
reserve_pool_size = ${PGBOUNCER_RESERVE_POOL_SIZE}
max_client_conn = ${PGBOUNCER_MAX_CLIENT_CONN}
server_idle_timeout = ${PGBOUNCER_SERVER_IDLE_TIMEOUT}
query_timeout = ${PGBOUNCER_QUERY_TIMEOUT}
client_idle_timeout = ${PGBOUNCER_CLIENT_IDLE_TIMEOUT}
ignore_startup_parameters = extra_float_digits,options
server_reset_query = DISCARD ALL
server_check_delay = 10
server_check_query = select 1
application_name_add_host = 1
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
admin_users = ${DB_USER}
stats_users = ${DB_USER}
CFG

# The userlist is generated at container start and never committed to source.
printf '"%s" "%s"\n' "$DB_USER" "$DB_PASSWORD" > /etc/pgbouncer/userlist.txt
chmod 600 /etc/pgbouncer/userlist.txt

exec pgbouncer /etc/pgbouncer/pgbouncer.ini
