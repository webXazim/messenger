FROM python:3.12-slim AS python-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
WORKDIR /build
COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Keep compilers and development headers out of the production image. ffmpeg is
# retained because background media jobs use it; curl is used by health checks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-builder /opt/venv /opt/venv
COPY . /app

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/staticfiles /app/media /app/private_media \
    && chmod +x /app/entrypoint.sh /app/deploy/django/start-server.sh \
    && python -m compileall -q /app/config /app/apps \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/app/deploy/django/start-server.sh"]
