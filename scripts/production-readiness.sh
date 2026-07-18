#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mode="full"
probe=0
deep=0
skip_public=0

for arg in "$@"; do
  case "$arg" in
    --preflight) mode="preflight" ;;
    --probe) probe=1 ;;
    --deep) deep=1 ;;
    --skip-public) skip_public=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

[[ -f .env ]] || { echo "Missing .env. Copy .env.production.example to .env first." >&2; exit 1; }

read_env() {
  local key="$1"
  local value
  value="$(sed -n "s/^${key}=//p" .env | tail -n 1 | tr -d '\r')"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

failures=()
warnings=()
require_value() {
  local name="$1" value
  value="$(read_env "$name")"
  if [[ -z "$value" || "$value" == REQUIRED_* || "$value" == *replace-me* || "$value" == *replace-with* || "$value" == *example.com* || "$value" == "203.0.113.10" ]]; then
    failures+=("$name is missing or still contains a placeholder value")
  fi
}

for name in APP_DOMAIN SITE_URL FRONTEND_BASE_URL ALLOWED_HOSTS CORS_ALLOWED_ORIGINS \
  CSRF_TRUSTED_ORIGINS SECRET_KEY DB_NAME DB_USER DB_PASSWORD DROPLET_PUBLIC_IP \
  TURN_REALM TURN_SHARED_SECRET TURN_URIS_JSON CLOUDFLARE_R2_ACCOUNT_ID \
  CLOUDFLARE_R2_BUCKET_NAME CLOUDFLARE_R2_ACCESS_KEY_ID \
  CLOUDFLARE_R2_SECRET_ACCESS_KEY; do
  require_value "$name"
done

app_domain="$(read_env APP_DOMAIN)"
site_url="$(read_env SITE_URL)"
frontend_url="$(read_env FRONTEND_BASE_URL)"
secret="$(read_env SECRET_KEY)"
turn_secret="$(read_env TURN_SHARED_SECRET)"
relay_min="$(read_env TURN_RELAY_MIN_PORT)"
relay_max="$(read_env TURN_RELAY_MAX_PORT)"

[[ "$site_url" == "https://${app_domain}" ]] || failures+=("SITE_URL must equal https://APP_DOMAIN")
[[ "$frontend_url" == "https://${app_domain}" ]] || failures+=("FRONTEND_BASE_URL must equal https://APP_DOMAIN")
[[ "$(read_env ALLOWED_HOSTS)" == "$app_domain" ]] || warnings+=("ALLOWED_HOSTS should contain only APP_DOMAIN")
(( ${#secret} >= 50 )) || failures+=("SECRET_KEY must contain at least 50 characters")
(( ${#turn_secret} >= 32 )) || failures+=("TURN_SHARED_SECRET must contain at least 32 characters")


for pair in   "DEBUG:False"   "MESSENGER_ENVIRONMENT:production"   "MESSENGER_REQUIRE_SECURE_SETTINGS:True"   "SECURE_SSL_REDIRECT:True"   "SESSION_COOKIE_SECURE:True"   "CSRF_COOKIE_SECURE:True"   "SECURE_PROXY_SSL_HEADER_ENABLED:True"   "USE_X_FORWARDED_HOST:True"   "CHAT_USE_R2_STORAGE:True"   "AUTH_REQUIRE_EMAIL_VERIFICATION:True"; do
  name="${pair%%:*}"
  expected="${pair#*:}"
  actual="$(read_env "$name")"
  [[ "${actual,,}" == "${expected,,}" ]] || failures+=("$name must be $expected in production")
done

[[ "$(read_env NGINX_CONF_PATH)" == "./nginx/snm.production.conf" ]] ||   failures+=("NGINX_CONF_PATH must be ./nginx/snm.production.conf")
[[ "$(read_env TURN_EXTERNAL_IP)" == "$(read_env DROPLET_PUBLIC_IP)" ]] ||   failures+=("TURN_EXTERNAL_IP must match DROPLET_PUBLIC_IP for this VPS deployment")
[[ "$(read_env TURN_REALM)" != "$app_domain" ]] ||   warnings+=("Use a separate DNS-only TURN hostname rather than APP_DOMAIN")

if [[ "$relay_min" =~ ^[0-9]+$ && "$relay_max" =~ ^[0-9]+$ ]]; then
  (( relay_min >= 1024 && relay_max <= 65535 && relay_min <= relay_max )) || failures+=("TURN relay range is invalid")
  (( relay_max - relay_min + 1 >= 20 )) || warnings+=("TURN relay range is narrow for concurrent calls")
else
  failures+=("TURN_RELAY_MIN_PORT and TURN_RELAY_MAX_PORT must be integers")
fi

require_value AUTH_PAYMENT_JWT_ISSUER
require_value AUTH_PAYMENT_JWT_AUDIENCE
jwt_algorithm="$(read_env AUTH_PAYMENT_JWT_ALGORITHM)"
if [[ "$(read_env CENTRAL_AUTH_ENABLED)" == "True" || "$(read_env CENTRAL_AUTH_ENABLED)" == "true" ]]; then
  require_value AUTH_PAYMENT_JWT_PUBLIC_KEY
  [[ "$jwt_algorithm" == RS* || "$jwt_algorithm" == ES* ]] || failures+=("Central authentication requires an asymmetric JWT algorithm such as RS256")
else
  require_value AUTH_PAYMENT_JWT_SIGNING_KEY
  local_jwt_secret="$(read_env AUTH_PAYMENT_JWT_SIGNING_KEY)"
  (( ${#local_jwt_secret} >= 64 )) || failures+=("AUTH_PAYMENT_JWT_SIGNING_KEY must contain at least 64 characters for standalone auth")
  [[ "$jwt_algorithm" == HS* ]] || failures+=("Standalone authentication requires an HMAC JWT algorithm such as HS256")
fi

if [[ "$(read_env CENTRAL_ADMIN_ENABLED)" == "True" || "$(read_env CENTRAL_ADMIN_ENABLED)" == "true" ]]; then
  require_value AUTH_PAYMENT_ADMIN_SERVICE_KEY
  require_value AUTH_PAYMENT_ADMIN_SIGNING_SECRET
fi

if [[ "$(read_env AUTH_REQUIRE_EMAIL_VERIFICATION)" == "True" || "$(read_env AUTH_REQUIRE_EMAIL_VERIFICATION)" == "true" ]]; then
  for name in EMAIL_HOST EMAIL_HOST_USER EMAIL_HOST_PASSWORD DEFAULT_FROM_EMAIL; do
    require_value "$name"
  done
fi

for command in docker openssl curl; do
  command -v "$command" >/dev/null || failures+=("Required command is not installed: $command")
done

python_bin="${PYTHON_BIN:-}"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 || command -v python || true)"
fi
[[ -n "$python_bin" ]] || failures+=("Required command is not installed: python3")

if [[ -f scripts/check-tls-certificate.sh ]]; then
  if ! tls_output="$(bash ./scripts/check-tls-certificate.sh secrets/tls/origin.crt secrets/tls/origin.key "$app_domain" 2>&1)"; then
    failures+=("TLS certificate check failed: $tls_output")
  else
    echo "$tls_output"
  fi
else
  failures+=("scripts/check-tls-certificate.sh is missing")
fi

cf_ranges="$(grep -c '^set_real_ip_from ' nginx/cloudflare-real-ip.conf 2>/dev/null || true)"
(( cf_ranges >= 10 )) || failures+=("Cloudflare trusted IP file is not populated; run scripts/update-cloudflare-ips.sh")

compose=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml)
if command -v docker >/dev/null; then
  "${compose[@]}" config >/dev/null || failures+=("Docker Compose configuration is invalid")
fi

if ((${#warnings[@]})); then
  echo "Warnings:"
  printf '  - %s\n' "${warnings[@]}"
fi
if ((${#failures[@]})); then
  echo "Readiness failures:" >&2
  printf '  - %s\n' "${failures[@]}" >&2
  exit 1
fi

echo "Production preflight passed."
[[ "$mode" == "preflight" ]] && exit 0

running_services="$("${compose[@]}" ps --status running --services)"
for service in postgres redis web worker beat frontend nginx turn; do
  if ! grep -qx "$service" <<<"$running_services"; then
    echo "Required service is not running: $service" >&2
    exit 1
  fi
done

"${compose[@]}" ps
"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T web python manage.py check --deploy
"${compose[@]}" exec -T web python manage.py migrate --check
"${compose[@]}" exec -T web python manage.py check_chat_readiness
"${compose[@]}" exec -T web python manage.py check_support_readiness
"${compose[@]}" exec -T worker celery -A config inspect ping --timeout=10
"${compose[@]}" exec -T web python manage.py check_object_storage

call_args=(python manage.py check_call_readiness)
(( probe )) && call_args+=(--probe)
"${compose[@]}" exec -T web "${call_args[@]}"

if (( deep )); then
  "${compose[@]}" exec -T web python manage.py check_chat_media --fail-on-missing
fi

curl --fail --silent --show-error --insecure \
  --resolve "${app_domain}:443:127.0.0.1" \
  "https://${app_domain}/api/v1/health/ready/" >/dev/null

echo "Origin HTTPS readiness endpoint passed."

if (( ! skip_public )); then
  curl --fail --silent --show-error --max-time 20 \
    "https://${app_domain}/api/v1/health/ready/" >/dev/null
  echo "Public Cloudflare HTTPS readiness endpoint passed."
fi

echo "Production readiness checks passed."
