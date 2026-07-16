#!/usr/bin/env bash
# Apply database migrations, then start the API server.
# Migrations are idempotent (alembic upgrade head); a restart re-runs safely.
set -euo pipefail

cd /app

echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] Starting API server on 0.0.0.0:8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
