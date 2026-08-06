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

# Presence on PATH is not proof of function. The Microsoft Store ships
# `python3` / `python` execution-alias stubs that resolve via `command -v`,
# print an advertisement instead of running, and are the first candidate on
# PATH under Git Bash. `command -v` never executes the file, so its exit status
# is never observed and the advertisement is captured as a version string.
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

# Python is reported only when a candidate proved it can execute code. When no
# candidate does, this now FAILS the check rather than reporting the stub's
# advertisement as a version and exiting 0.
check_python() {
  local candidate version
  if ! candidate="$(find_working_python)"; then
    printf '  [MISSING] Python: no working interpreter found (tried python3, python, py). Install Python 3.13 or newer.\n'
    printf '            A `python3` that only prints a Microsoft Store advertisement does not count.\n'
    missing+=("Python")
    return
  fi
  version="$("${candidate}" --version 2>&1 | head -n1 || true)"
  printf '  [ok]      Python: %s (%s)\n' "${version}" "$(command -v "${candidate}")"
}

echo "FreeTier Atlas environment check"
echo "Repository root: ${REPO_ROOT}"
echo ""

check_runtime "Docker" "docker" "Install Docker Desktop or the Docker Engine."
check_runtime "Node.js" "node" "Install Node.js 20 or newer."
check_runtime "npm" "npm" "npm ships with Node.js."
check_python

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
