#!/usr/bin/env bash
# Smoke-test the running FreeTier Atlas stack against ground truth.
#
# Verifies the live API liveness endpoint (200), readiness endpoint (200 with
# database reachable), and that the Alembic baseline migration created the
# scaffold app_meta table with its marker row. Resolves the repository root from
# the script's own path. Requires the stack to be running (stack-up).
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

API_PORT="${API_PORT:-8000}"
PG_USER="${POSTGRES_USER:-atlas}"
PG_DB="${POSTGRES_DB:-atlas}"

failures=()

run_check() {
  local name="$1"
  shift
  echo "==> ${name}"
  if "$@"; then
    echo "    PASS: ${name}"
  else
    echo "    FAIL: ${name}"
    failures+=("${name}")
  fi
}

check_liveness() {
  local body
  body="$(curl -fsS -m 5 "http://localhost:${API_PORT}/health")" || return 1
  echo "${body}" | grep -q '"status":"ok"'
}

check_readiness() {
  local body
  body="$(curl -fsS -m 5 "http://localhost:${API_PORT}/health/ready")" || return 1
  echo "${body}" | grep -q '"status":"ready"' && echo "${body}" | grep -q '"database":"ok"'
}

check_migration() {
  local regclass marker
  regclass="$(docker compose exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc "SELECT to_regclass('public.app_meta')" | tr -d '[:space:]')"
  [[ "${regclass}" == "app_meta" ]] || return 1
  marker="$(docker compose exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc "SELECT value FROM app_meta WHERE key='scaffold_initialized'" | tr -d '[:space:]')"
  [[ "${marker}" == "true" ]]
}

run_check "API liveness (/health = 200)" check_liveness
run_check "API readiness (/health/ready = 200, db ok)" check_readiness
run_check "Migration applied (app_meta table + marker row)" check_migration

echo ""
if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "STACK SMOKE FAILED: ${failures[*]}"
  exit 1
fi
echo "STACK SMOKE PASSED"
