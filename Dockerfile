# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# --- System dependencies ---
# ffmpeg: required for music playback.
# ca-certificates: required for outbound TLS (Discord gateway/API, yt-dlp).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Non-root user ---
RUN groupadd --system c3p0 && useradd --system --gid c3p0 --create-home c3p0

WORKDIR /app

# Python buffers stdout fully (not line-by-line) whenever it isn't attached
# to a TTY, which is always true under Docker - without this, log lines sit
# in an internal buffer and never reach `docker logs` in anything resembling
# real time.
ENV PYTHONUNBUFFERED=1

# --- Python dependencies ---
# Copy only dependency metadata first so this layer is cached unless
# pyproject.toml actually changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# --- Application code ---
COPY app ./app
COPY alembic.ini ./alembic.ini

# Persistent data volume (SQLite database, health-check marker file).
RUN mkdir -p /data && chown -R c3p0:c3p0 /data /app

USER c3p0

EXPOSE 8000
# Web dashboard (c3p0-web service, entrypoint overridden in compose.yaml to
# `python -m app.web`) - documented here for parity, not used by this
# image's own default entrypoint below.
EXPOSE 8080
# Internal music control-plane API (app/music/internal_api.py) - c3p0
# service only, reachable from c3p0-web over the Compose network by
# service name. Never published in compose.yaml; see its ports: comment.
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -m app.health || exit 1

ENTRYPOINT ["python", "-m", "app"]
