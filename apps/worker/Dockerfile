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
