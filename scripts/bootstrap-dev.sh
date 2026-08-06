#!/usr/bin/env bash
# Bootstrap the local development environment for FreeTier Atlas.
#
# Creates the Python virtual environment in .venv (if absent), upgrades pip,
# installs the project with runtime and dev dependencies, and installs Node dev
# dependencies. Resolves the repository root from this script's own path.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

# Presence on PATH is not proof of function. The Microsoft Store ships
# `python3` / `python` execution-alias stubs that resolve via `command -v`,
# print an advertisement instead of running, and are the first candidate on
# PATH under Git Bash. `command -v` never executes the file, so its exit status
# is never observed.
#
# Accept a candidate only when it actually evaluates a trivial program and
# prints the expected sentinel. Validate on the OUTPUT, not on presence and not
# on exit status: the Store stub happens to exit 49, but an equivalent stub
# exiting 0 would be indistinguishable from success.
#
# This mirrors `_stack_python` in scripts/stack-env.sh; the two are deliberately
# independent so each script stays a standalone entry point.
find_working_python() {
  local candidate probe
  for candidate in python3 python py; do
    command -v "${candidate}" >/dev/null 2>&1 || continue
    probe="$("${candidate}" -c 'print("atlas-python-ok")' 2>/dev/null </dev/null || true)"
    probe="${probe//$'\r'/}"
    if [[ "${probe}" == "atlas-python-ok" ]]; then
      printf '%s' "${candidate}"
      return 0
    fi
  done
  return 1
}

if ! PYTHON="$(find_working_python)"; then
  echo "ERROR: no working Python interpreter was found on PATH (tried python3, python, py). Install Python 3.13+ and retry." >&2
  echo "       A 'python3' that only prints a Microsoft Store advertisement does not count." >&2
  exit 1
fi

VENV="${REPO_ROOT}/.venv"
if [[ -x "${VENV}/bin/python" ]]; then
  VENV_PY="${VENV}/bin/python"
elif [[ -x "${VENV}/Scripts/python.exe" ]]; then
  VENV_PY="${VENV}/Scripts/python.exe"
else
  VENV_PY=""
fi

if [[ -z "${VENV_PY}" ]]; then
  echo "==> Creating virtual environment (.venv)"
  "${PYTHON}" -m venv "${VENV}"
  if [[ -x "${VENV}/bin/python" ]]; then
    VENV_PY="${VENV}/bin/python"
  else
    VENV_PY="${VENV}/Scripts/python.exe"
  fi
else
  echo "==> Reusing existing virtual environment (.venv)"
fi

echo "==> Upgrading pip"
"${VENV_PY}" -m pip install --upgrade pip

echo "==> Installing Python project with dev dependencies"
"${VENV_PY}" -m pip install -e ".[dev]"

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is required but was not found on PATH. Install Node.js 20+ and retry." >&2
  exit 1
fi

echo "==> Installing Node dev dependencies (npm install)"
npm install

WEB_DIR="${REPO_ROOT}/apps/web"
if [[ -f "${WEB_DIR}/package.json" ]]; then
  echo "==> Installing web frontend dependencies (apps/web)"
  ( cd "${WEB_DIR}" && npm install )
fi

echo ""
echo "BOOTSTRAP COMPLETE"
echo "Next: scripts/test.sh to run tests, or scripts/stack-up.sh to start the stack."
