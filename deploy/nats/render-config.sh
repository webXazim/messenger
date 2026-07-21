#!/bin/sh
set -eu

template=${NATS_CONFIG_TEMPLATE:-/etc/nats/nats.conf.template}
output=${NATS_CONFIG_OUTPUT:-/tmp/nats.conf}

fail() {
  echo "nats-config: $*" >&2
  exit 1
}

require_value() {
  name=$1
  eval "value=\${$name:-}"
  [ -n "$value" ] || fail "$name is required"
  without_linebreaks=$(printf '%s' "$value" | tr -d '\r\n')
  [ "$value" = "$without_linebreaks" ] || fail "$name must not contain line breaks"
}

# Escape a value first for a NATS double-quoted string, then for use as a sed
# replacement. This supports numeric-leading and punctuation-rich secrets
# without exposing them to the NATS configuration lexer.
escaped_replacement() {
  printf '%s' "$1" |
    sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' |
    sed -e 's/[\\&|]/\\&/g'
}

require_value NATS_APP_USER
require_value NATS_APP_PASSWORD
require_value NATS_REALTIME_USER
require_value NATS_REALTIME_PASSWORD
[ -r "$template" ] || fail "configuration template is not readable: $template"

app_user=$(escaped_replacement "$NATS_APP_USER")
app_password=$(escaped_replacement "$NATS_APP_PASSWORD")
realtime_user=$(escaped_replacement "$NATS_REALTIME_USER")
realtime_password=$(escaped_replacement "$NATS_REALTIME_PASSWORD")

umask 077
sed \
  -e "s|__NATS_APP_USER__|$app_user|g" \
  -e "s|__NATS_APP_PASSWORD__|$app_password|g" \
  -e "s|__NATS_REALTIME_USER__|$realtime_user|g" \
  -e "s|__NATS_REALTIME_PASSWORD__|$realtime_password|g" \
  "$template" > "$output"

grep -q '__NATS_' "$output" && fail "configuration contains an unresolved credential placeholder"

if [ "${NATS_CONFIG_RENDER_ONLY:-0}" = "1" ]; then
  exit 0
fi

server_bin=$(command -v nats-server || true)
[ -n "$server_bin" ] || fail "nats-server executable was not found in PATH"

if [ "${NATS_CONFIG_TEST_ONLY:-0}" = "1" ]; then
  exec "$server_bin" --config "$output" -t
fi

exec "$server_bin" --config "$output"
