#!/usr/bin/env bash
# Verify the local development runtimes required by FreeTier Atlas.
#
# Checks Docker (with a running daemon), Node.js, npm, and Python and prints
# their versions. Exits non-zero with an actionable message when a required
# runtime is missing. Does not print secrets or full environment dumps. Resolves
# the repository root from this script's own path.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

missing=()

check_runtime() {
  local name="$1" command="$2" hint="$3"
  if ! command -v "${command}" >/dev/null 2>&1; then
    printf '  [MISSING] %s: '\''%s'\'' not found on PATH. %s\n' "${name}" "${command}" "${hint}"
    missing+=("${name}")
    return
  fi
  local version
  version="$("${command}" --version 2>&1 | head -n1 || true)"
  printf '  [ok]      %s: %s\n' "${name}" "${version}"
}

echo "FreeTier Atlas environment check"
echo "Repository root: ${REPO_ROOT}"
echo ""

check_runtime "Docker" "docker" "Install Docker Desktop or the Docker Engine."
check_runtime "Node.js" "node" "Install Node.js 20 or newer."
check_runtime "npm" "npm" "npm ships with Node.js."
if command -v python3 >/dev/null 2>&1; then
  check_runtime "Python" "python3" "Install Python 3.13 or newer."
else
  check_runtime "Python" "python" "Install Python 3.13 or newer."
fi

# Verify the Docker daemon is reachable, not just the CLI.
if command -v docker >/dev/null 2>&1; then
  if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
    echo "  [ok]      Docker daemon: reachable"
  else
    echo "  [MISSING] Docker daemon: not reachable. Start Docker and retry."
    missing+=("Docker daemon")
  fi
fi

echo ""
if [[ "${#missing[@]}" -gt 0 ]]; then
  echo "ENVIRONMENT CHECK FAILED: missing ${missing[*]}"
  exit 1
fi
echo "ENVIRONMENT CHECK PASSED"
exit 0
