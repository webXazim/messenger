FROM rust:1.85-bookworm AS builder
WORKDIR /app
ENV CARGO_INCREMENTAL=0 \
    CARGO_BUILD_JOBS=1
COPY . .
RUN if [ -f Cargo.lock ]; then \
      cargo build --release --locked --jobs 1; \
    else \
      cargo build --release --jobs 1; \
    fi

FROM debian:bookworm-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --create-home realtime
COPY --from=builder /app/target/release/crescentsphere-realtime /usr/local/bin/realtime
USER realtime
EXPOSE 9000
ENTRYPOINT ["/usr/local/bin/realtime"]
