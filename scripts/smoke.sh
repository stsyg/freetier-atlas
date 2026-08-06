#!/usr/bin/env bash
set -u

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || fail "unable to resolve script directory"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)" || fail "unable to resolve repository root"

required_files=(
  "AGENTS.md"
  "docs/AGENT_HARNESS.md"
  "PLAN.md"
  "CODEX_TASKS.md"
  "docs/MVP_ACCEPTANCE.md"
  "docs/DECISIONS.md"
  "agent-state/feature_list.json"
  "agent-state/progress.md"
  "agent-state/current_contract.json"
  "agent-state/evaluation.json"
)

json_files=(
  "agent-state/feature_list.json"
  "agent-state/current_contract.json"
  "agent-state/evaluation.json"
)

# Presence on PATH is not proof of function. The Microsoft Store ships
# `python3` / `python` execution-alias stubs that resolve via `command -v`,
# print an advertisement instead of running, and are the first candidate on
# PATH under Git Bash. `command -v` never executes the file, so its exit status
# is never observed and the JSON validation below then runs a program that
# cannot execute anything.
#
# Accept a candidate only when it actually evaluates a trivial program and
# prints the expected sentinel. Validate on the OUTPUT, not on presence and not
# on exit status: the Store stub happens to exit 49, but an equivalent stub
# exiting 0 would be indistinguishable from success.
#
# This mirrors `_stack_python` in scripts/stack-env.sh; the two are deliberately
# independent so each script stays a standalone entry point.
find_python() {
  local candidate probe
  for candidate in python3 python py; do
    command -v "${candidate}" >/dev/null 2>&1 || continue
    probe="$("${candidate}" -c 'print("atlas-python-ok")' 2>/dev/null </dev/null || true)"
    probe="${probe//$'\r'/}"
    if [[ "${probe}" == "atlas-python-ok" ]]; then
      command -v "${candidate}"
      return 0
    fi
  done
  return 1
}

validate_json() {
  local python_bin="$1"
  local relative_path="$2"
  local full_path="${repo_root}/${relative_path}"

  "$python_bin" -c 'import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    with path.open("r", encoding="utf-8") as handle:
        json.load(handle)
except Exception as exc:
    print(f"ERROR: invalid JSON in {sys.argv[2]}: {exc}", file=sys.stderr)
    sys.exit(1)
' "$full_path" "$relative_path"
}

info "FreeTier Atlas F000 smoke checks"
info "Repository root: ${repo_root}"

for relative_path in "${required_files[@]}"; do
  if [[ ! -f "${repo_root}/${relative_path}" ]]; then
    fail "required repository file is missing: ${relative_path}"
  fi
done
info "Required repository files: ok"

python_bin="$(find_python)" || fail "no working Python interpreter was found (tried python3, python, py); a 'python3' that only prints a Microsoft Store advertisement does not count. Install Python and rerun scripts/smoke.sh"
for relative_path in "${json_files[@]}"; do
  validate_json "$python_bin" "$relative_path" || exit 1
done
info "Agent-state JSON syntax: ok"

if command -v pwsh >/dev/null 2>&1; then
  info "PowerShell verification available: pwsh"
elif command -v powershell.exe >/dev/null 2>&1 || command -v powershell >/dev/null 2>&1; then
  info "PowerShell verification available: Windows PowerShell"
else
  info "PowerShell verification: unverified - neither pwsh nor Windows PowerShell is available in this environment"
fi

info "Application scaffold checks: pending F002 - product application health was not checked because the app stack does not exist yet"
info "F000 smoke checks completed"
