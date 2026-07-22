FROM rust:1.88-bookworm AS builder
WORKDIR /app
ENV CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=1
COPY . .
RUN test -s Cargo.lock || { echo "Cargo.lock is required; run ./scripts/generate-media-worker-lockfile.sh first" >&2; exit 1; }
RUN --mount=type=cache,id=messenger-media-worker-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=messenger-media-worker-cargo-git,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,id=messenger-media-worker-target,target=/app/target,sharing=locked \
    cargo build --release --locked --jobs 1 \
    && install -D -m 0755 /app/target/release/crescentsphere-media-worker /out/media-worker

FROM debian:bookworm-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10002 --create-home mediaworker \
    && mkdir -p /work /app/private_media \
    && chown -R mediaworker:mediaworker /work /app/private_media
COPY --from=builder /out/media-worker /usr/local/bin/media-worker
USER mediaworker
ENTRYPOINT ["/usr/local/bin/media-worker"]
