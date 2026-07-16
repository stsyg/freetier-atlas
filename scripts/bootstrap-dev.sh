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

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "ERROR: Python is required but was not found on PATH. Install Python 3.13+ and retry." >&2
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
