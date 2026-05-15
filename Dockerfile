# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install dependencies first (cached layer, independent of source changes).
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Install the application itself.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Bundle the default (non-secret) config.
COPY config ./config

# Run as a non-root user; data dir is a mounted volume.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=5m --timeout=10s --start-period=20s --retries=3 \
    CMD ["tender-agent", "healthcheck"]

# Default: run the blocking scheduler (daily cron from SCHEDULE_CRON).
ENTRYPOINT ["tender-agent"]
CMD ["schedule"]
