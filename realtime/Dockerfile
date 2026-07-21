FROM rust:1.88-bookworm AS builder
WORKDIR /app
ENV CARGO_INCREMENTAL=0 \
    CARGO_BUILD_JOBS=1
COPY . .
RUN test -s Cargo.lock || { echo "Cargo.lock is required; run ./scripts/generate-realtime-lockfile.sh first" >&2; exit 1; }
RUN cargo build --release --locked --jobs 1

FROM debian:bookworm-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --create-home realtime
COPY --from=builder /app/target/release/crescentsphere-realtime /usr/local/bin/realtime
USER realtime
EXPOSE 9000
ENTRYPOINT ["/usr/local/bin/realtime"]
