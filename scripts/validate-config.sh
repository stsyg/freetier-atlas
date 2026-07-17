#!/usr/bin/env bash
# Validate FreeTier Atlas declarative YAML configuration files.
#
# Runs the config validation CLI (app.config.cli) over the given files. With no
# arguments, validates every *.yaml under config/examples. Prefers the Python
# interpreter from a local .venv when present. Resolves the repository root from
# this script's path. Never prints secrets.
#
# Exit code 0 when every file validates; non-zero otherwise.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
elif [[ -x "${REPO_ROOT}/.venv/Scripts/python.exe" ]]; then
  PYTHON="${REPO_ROOT}/.venv/Scripts/python.exe"
else
  PYTHON="python"
  echo "Note: .venv python not found; using python from PATH. Run scripts/bootstrap-dev.sh first for a pinned environment."
fi

export PYTHONPATH="${REPO_ROOT}/apps/api"

if [[ $# -eq 0 ]]; then
  mapfile -t FILES < <(find config/examples -name '*.yaml' | sort)
  set -- "${FILES[@]}"
fi

"${PYTHON}" -m app.config.cli validate "$@"
