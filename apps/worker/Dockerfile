# syntax=docker/dockerfile:1

# FreeTier Atlas worker/scheduler image.
# Build context is the repository root to mirror the API image layout. The same
# image runs both the worker (default command) and the scheduler (command
# override in docker-compose.yml). Migrations are owned by the API service.
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Optional package-index overrides for networks that cannot reach public PyPI.
# Declared with NO default, exactly as in apps/api/Dockerfile: an unsupplied
# build arg is absent from the build environment rather than empty, so an
# unconfigured build resolves from public PyPI exactly as before and the
# `pip install` below is unchanged. Supplied values are read natively by pip
# from its environment. Never commit a value.
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG PIP_TRUSTED_HOST

# Install runtime dependencies first for better layer caching.
COPY apps/worker/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Application code (the `worker` package lives under apps/worker/worker).
COPY apps/worker/ /app/apps/worker/

# Run as a non-root user.
RUN useradd --create-home --uid 10002 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONPATH=/app/apps/worker

# Default to the worker; the scheduler service overrides this command.
CMD ["python", "-m", "worker.main"]
