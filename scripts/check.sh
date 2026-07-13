#!/usr/bin/env bash
# Run the FreeTier Atlas F001 repository checks locally, mirroring CI.
#
# Runs Ruff lint, Ruff format check, pytest, Prettier check, ESLint, a
# detect-secrets scan against the committed baseline, and a Python dependency
# audit. Resolves the repository root from this script's own path so it can be
# invoked from any working directory. Prefers tools from a local .venv when
# present and falls back to tools on PATH.
#
# Exit code 0 when all checks pass; non-zero when any check fails.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

RUN_NODE_AUDIT=0
if [[ "${1:-}" == "--node-audit" ]]; then
  RUN_NODE_AUDIT=1
fi

# Locate a tool from the local virtualenv (POSIX bin/ or Windows Scripts/) or PATH.
resolve_tool() {
  local name="$1"
  if [[ -x "${REPO_ROOT}/.venv/bin/${name}" ]]; then
    echo "${REPO_ROOT}/.venv/bin/${name}"
  elif [[ -x "${REPO_ROOT}/.venv/Scripts/${name}.exe" ]]; then
    echo "${REPO_ROOT}/.venv/Scripts/${name}.exe"
  else
    echo "${name}"
  fi
}

RUFF="$(resolve_tool ruff)"
PYTEST="$(resolve_tool pytest)"
DETECT_HOOK="$(resolve_tool detect-secrets-hook)"
PIP_AUDIT="$(resolve_tool pip-audit)"

FAILURES=()

check() {
  local name="$1"
  shift
  echo ""
  echo "==> ${name}"
  if "$@"; then
    echo "    PASS: ${name}"
  else
    echo "    FAIL: ${name}"
    FAILURES+=("${name}")
  fi
}

secret_scan() {
  # shellcheck disable=SC2046
  git ls-files -z | xargs -0 "${DETECT_HOOK}" --baseline .secrets.baseline
}

check "Ruff lint" "${RUFF}" check .
check "Ruff format check" "${RUFF}" format --check .
check "Pytest" "${PYTEST}" -q
check "Prettier check" npm run --silent format:check
check "ESLint" npm run --silent lint
check "Secret scan" secret_scan
check "Python dependency audit" "${PIP_AUDIT}" -r requirements-dev.txt

if [[ "${RUN_NODE_AUDIT}" -eq 1 ]]; then
  check "Node dependency audit" npm audit --omit=dev --audit-level=high
fi

echo ""
if [[ "${#FAILURES[@]}" -gt 0 ]]; then
  echo "CHECKS FAILED: ${FAILURES[*]}"
  exit 1
fi
echo "ALL CHECKS PASSED"
exit 0
