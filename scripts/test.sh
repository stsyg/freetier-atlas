#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || fail "unable to resolve script directory"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)" || fail "unable to resolve repository root"

cd "$repo_root"

"${BASH}" "${script_dir}/init.sh"
"${BASH}" "${script_dir}/smoke.sh"

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

find_npm() {
  local candidate
  for candidate in npm.cmd npm; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

python_bin="$(find_python)" || fail "Python is required to run repository checks"
npm_bin="$(find_npm)" || fail "npm is required to run Node repository checks"

"$python_bin" tools/repo_checks.py all
"$python_bin" -m unittest discover -s tests -p "test_*.py"
"$npm_bin" test
git diff --check
