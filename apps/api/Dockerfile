# syntax=docker/dockerfile:1

# FreeTier Atlas API image.
# Build context is the repository root so the image can include both the
# application package (apps/api) and the Alembic migrations (migrations/).
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime dependencies first for better layer caching.
COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Application code and migrations.
COPY apps/api/ /app/apps/api/
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini

# Run as a non-root user.
RUN chmod +x /app/apps/api/entrypoint.sh \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONPATH=/app/apps/api

EXPOSE 8000

ENTRYPOINT ["/app/apps/api/entrypoint.sh"]
