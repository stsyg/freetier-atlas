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

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
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

info "FreeTier Atlas F000 initialization checks"
info "Repository root: ${repo_root}"

for relative_path in "${required_files[@]}"; do
  if [[ ! -f "${repo_root}/${relative_path}" ]]; then
    fail "required repository file is missing: ${relative_path}"
  fi
done
info "Required repository files: ok"

python_bin="$(find_python)" || fail "Python is required to validate JSON syntax; install python3 or python and rerun scripts/init.sh"
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

info "Application scaffold checks: pending F002 - no product services are started by F000 initialization"
info "F000 initialization checks completed"
