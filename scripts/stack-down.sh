#!/usr/bin/env bash
# Stop and remove the FreeTier Atlas development stack.
#
# Runs `docker compose down`. With --volumes, also removes the PostgreSQL data
# volume (destroys local database data). Resolves the repository root from the
# script's own path.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

if [[ "${1:-}" == "--volumes" ]]; then
  echo "==> docker compose down --volumes (removes database data)"
  docker compose down --volumes
else
  echo "==> docker compose down"
  docker compose down
fi

echo "STACK DOWN"
