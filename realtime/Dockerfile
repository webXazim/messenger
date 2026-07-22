FROM rust:1.88-bookworm AS builder
WORKDIR /app
ENV CARGO_INCREMENTAL=0 \
    CARGO_BUILD_JOBS=1
COPY . .
RUN test -s Cargo.lock || { echo "Cargo.lock is required; run ./scripts/generate-realtime-lockfile.sh first" >&2; exit 1; }
RUN --mount=type=cache,id=messenger-realtime-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=messenger-realtime-cargo-git,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,id=messenger-realtime-target,target=/app/target,sharing=locked \
    cargo build --release --locked --jobs 1 \
    && install -D -m 0755 /app/target/release/crescentsphere-realtime /out/realtime

FROM debian:bookworm-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --create-home realtime
COPY --from=builder /out/realtime /usr/local/bin/realtime
USER realtime
EXPOSE 9000
ENTRYPOINT ["/usr/local/bin/realtime"]
