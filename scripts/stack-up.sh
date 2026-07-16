#!/usr/bin/env bash
# Build and start the FreeTier Atlas development stack.
#
# Runs `docker compose up -d --build` for the postgres and api services and
# waits until the API liveness endpoint responds. Resolves the repository root
# from this script's own path.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

TIMEOUT_SECONDS="${STACK_UP_TIMEOUT:-120}"
API_PORT="${API_PORT:-8000}"
HEALTH_URL="http://localhost:${API_PORT}/health"

echo "==> docker compose up -d --build"
if ! docker compose up -d --build; then
  echo "ERROR: docker compose up failed" >&2
  exit 1
fi

echo "==> Waiting for API liveness at ${HEALTH_URL} (timeout ${TIMEOUT_SECONDS}s)"
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
live=0
while [[ "$(date +%s)" -lt "${deadline}" ]]; do
  if curl -fsS -m 5 "${HEALTH_URL}" >/dev/null 2>&1; then
    live=1
    break
  fi
  sleep 3
done

if [[ "${live}" -ne 1 ]]; then
  echo "STACK UP FAILED: API did not become live within ${TIMEOUT_SECONDS}s."
  echo "Recent api logs:"
  docker compose logs --tail 40 api || true
  exit 1
fi

echo ""
echo "STACK UP: API is live at ${HEALTH_URL}"
echo "Next: scripts/stack-smoke.sh to verify readiness and migrations."
