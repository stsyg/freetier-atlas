#!/usr/bin/env bash
# Run the FreeTier Atlas test suite.
#
# Runs pytest (preferring tools from .venv). With --full, also runs the full
# repository check suite (scripts/check.sh --node-audit). Remaining arguments are
# passed through to pytest. Resolves the repository root from the script's path.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

RUN_FULL=0
PYTEST_ARGS=()
for arg in "$@"; do
  if [[ "${arg}" == "--full" ]]; then
    RUN_FULL=1
  else
    PYTEST_ARGS+=("${arg}")
  fi
done

if [[ -x "${REPO_ROOT}/.venv/bin/pytest" ]]; then
  PYTEST="${REPO_ROOT}/.venv/bin/pytest"
elif [[ -x "${REPO_ROOT}/.venv/Scripts/pytest.exe" ]]; then
  PYTEST="${REPO_ROOT}/.venv/Scripts/pytest.exe"
else
  PYTEST="pytest"
  echo "Note: .venv pytest not found; using pytest from PATH. Run scripts/bootstrap-dev.sh first for a pinned environment."
fi

echo "==> pytest"
if ! "${PYTEST}" "${PYTEST_ARGS[@]}"; then
  echo "TESTS FAILED"
  exit 1
fi

if [[ "${RUN_FULL}" -eq 1 ]]; then
  echo "==> Full check suite (scripts/check.sh --node-audit)"
  if ! bash "${SCRIPT_DIR}/check.sh" --node-audit; then
    exit 1
  fi
fi

echo ""
echo "TESTS PASSED"
