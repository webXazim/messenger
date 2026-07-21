FROM alpine:3.22

RUN apk add --no-cache pgbouncer postgresql-client gettext

COPY entrypoint.sh /usr/local/bin/pgbouncer-entrypoint
RUN chmod +x /usr/local/bin/pgbouncer-entrypoint \
    && addgroup -S pgbouncer \
    && adduser -S -G pgbouncer pgbouncer \
    && mkdir -p /etc/pgbouncer /var/run/pgbouncer \
    && chown -R pgbouncer:pgbouncer /etc/pgbouncer /var/run/pgbouncer

USER pgbouncer
EXPOSE 6432
ENTRYPOINT ["/usr/local/bin/pgbouncer-entrypoint"]
