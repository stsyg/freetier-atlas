#!/usr/bin/env bash
# Export the read-only catalogue to a deterministic static JSON snapshot.
#
# Thin wrapper around `python -m app.export` (see apps/api/app/export). It puts
# apps/api on PYTHONPATH so the `app` package imports without an editable
# install, prefers the pinned .venv interpreter, and passes every argument
# through to the CLI. Requires DATABASE_URL to point at the catalogue database.
#
# Usage:
#   DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
#     scripts/export-catalogue.sh --out dist/catalogue [--as-of 2026-06-01T12:00:00+00:00]
#
# The snapshot is a pure function of (database state, --as-of): the same state
# and --as-of yield byte-identical output, so a published snapshot can be
# diffed and re-verified.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
elif [[ -x "${REPO_ROOT}/.venv/Scripts/python.exe" ]]; then
  PYTHON="${REPO_ROOT}/.venv/Scripts/python.exe"
else
  PYTHON="python3"
  echo "Note: .venv python not found; using python3 from PATH. Run scripts/bootstrap-dev.sh first for a pinned environment."
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set; the export reads the catalogue from the database." >&2
  exit 2
fi

export PYTHONPATH="${REPO_ROOT}/apps/api${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON}" -m app.export "$@"
