#!/usr/bin/env bash
# Run the FreeTier Atlas F001 repository checks locally, mirroring CI.
#
# Runs Ruff lint, Ruff format check, pytest, Prettier check, ESLint, a
# detect-secrets scan against the committed baseline, a URL host allowlist
# check, and a Python dependency audit. Resolves the repository root from this
# script's own path so it can be invoked from any working directory. Prefers
# tools from a local .venv when present and falls back to tools on PATH.
#
# Exit code 0 when all checks pass; non-zero when any check fails.
#
# REPORTING RULE, and it is why several branches below are longer than the work
# they do: a branch may assert only what it has ESTABLISHED. Turning an exit
# status into a specific stated cause is permitted only where this script has
# independently measured that cause. Everywhere else it reports the status it
# actually observed and surfaces the tool's own words instead.
#
# That rule was paid for. An earlier version mapped detect-secrets-hook exit 1
# to one sentence, "detect-secrets found a secret that is not in the baseline".
# Exit 1 is also what that hook returns for an UNSTAGED baseline, which is the
# condition that actually occurred, so the summary announced a secret leak that
# did not exist. A builder trusting it raises a false alarm; one who "fixes" it
# by editing the baseline does real damage. Two further false alarms on this
# project had the same shape: absent node_modules reported as a Prettier and an
# ESLint failure.
#
# The defect has a quieter, symmetric form that is easy to leave instrumented on
# one side only, so both are handled here: asserting a PASS that was never
# established either. See the empty-file-list branch of secret_scan.
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

# resolve_tool falls back to the bare NAME when it finds nothing, so its return
# value alone does not establish that a program exists. Asking before running is
# what lets a missing toolchain report itself instead of surfacing as a lint,
# formatting or audit verdict.
tool_available() {
  local candidate="$1"
  if [[ -x "${candidate}" ]]; then
    return 0
  fi
  command -v -- "${candidate}" >/dev/null 2>&1
}

RUFF="$(resolve_tool ruff)"
PYTEST="$(resolve_tool pytest)"
DETECT_HOOK="$(resolve_tool detect-secrets-hook)"
PIP_AUDIT="$(resolve_tool pip-audit)"
PYTHON="$(resolve_tool python)"

FAILURES=()

# A check function may set this to state a cause it has ESTABLISHED. When it is
# empty, check() reports the exit status it observed and nothing more, because
# that is all it knows.
FAILURE_REASON=""

add_failure() {
  local name="$1"
  local reason="$2"
  echo "    FAIL: ${name} (${reason})"
  FAILURES+=("${name}: ${reason}")
}

# check [--tool NAME]... [--path RELATIVE]... "Display name" command [args...]
#
# Preconditions are declared rather than discovered from a non-zero exit
# afterwards, so an absent program or an uninstalled dependency reports ITSELF.
check() {
  local tools=()
  local paths=()
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --tool)
        tools+=("$2")
        shift 2
        ;;
      --path)
        paths+=("$2")
        shift 2
        ;;
      *)
        break
        ;;
    esac
  done

  local name="$1"
  shift
  echo ""
  echo "==> ${name}"

  local tool
  if [[ "${#tools[@]}" -gt 0 ]]; then
    for tool in "${tools[@]}"; do
      if ! tool_available "${tool}"; then
        add_failure "${name}" "required tool '${tool}' was not found in .venv nor on PATH. This is a missing toolchain, NOT a finding from this check"
        return 0
      fi
    done
  fi

  local needed
  if [[ "${#paths[@]}" -gt 0 ]]; then
    for needed in "${paths[@]}"; do
      if [[ ! -e "${REPO_ROOT}/${needed}" ]]; then
        add_failure "${name}" "required path '${needed}' is missing; run the install step that creates it. This is a missing dependency, NOT a finding from this check"
        return 0
      fi
    done
  fi

  FAILURE_REASON=""
  local status=0
  "$@" || status=$?
  if [[ "${status}" -eq 0 ]]; then
    echo "    PASS: ${name}"
    return 0
  fi
  local reason="${FAILURE_REASON}"
  if [[ -z "${reason}" ]]; then
    reason="exit code ${status}"
  fi
  add_failure "${name}" "${reason}"
}

secret_scan() {
  local status=0
  local files=()
  # Null-delimited into an array, then passed as "${files[@]}". This is the only
  # form that keeps BOTH properties; each earlier form kept one and lost the other.
  #
  #   git ls-files -z | xargs -0 ...   null-safe, but xargs collapses every child
  #                                    exit code from 1..125 into 123, so a secret
  #                                    finding (1) and a baseline rewrite (3) become
  #                                    indistinguishable.
  #   ... $(git ls-files)              preserves the exit code, but word-splits on
  #                                    whitespace. MEASURED: a secret planted in
  #                                    'q2 dir/has space.txt' returns 0 - a silent
  #                                    pass on a real secret.
  #
  # A `while read` loop rather than `mapfile -d ''`: mapfile does not exist at all
  # on bash 3.2, which is still the system bash on macOS, a platform this script
  # claims to support. Verified on 3.2.57 and 5.2.37.
  while IFS= read -r -d '' file; do
    files+=("$file")
  done < <(git ls-files -z)

  if [[ "${#files[@]}" -eq 0 ]]; then
    # MEASURED: this hook exits 0 when handed no filenames. Passing here would
    # report a clean scan of nothing at all - the same defect as the exit-1
    # branch below, pointing the reassuring way instead of the alarming one and
    # therefore far likelier to go unnoticed. It is also the case that would
    # expand "${files[@]}" under `set -u` on bash 3.2 and abort the script.
    FAILURE_REASON="git ls-files listed no files, so nothing would have been scanned"
    return 1
  fi

  # Establish a REWRITE by comparing the bytes, not by reading it off an exit
  # code. cmp is POSIX and always present, unlike sha256sum, which is absent on
  # macOS where the digest tool is spelled differently.
  local baseline_copy=""
  if [[ -f .secrets.baseline ]]; then
    baseline_copy="$(mktemp)"
    cp -- .secrets.baseline "${baseline_copy}"
  fi

  local out_file err_file
  out_file="$(mktemp)"
  err_file="$(mktemp)"
  "${DETECT_HOOK}" --baseline .secrets.baseline "${files[@]}" >"${out_file}" 2>"${err_file}" || status=$?

  local rewritten=0
  if [[ -n "${baseline_copy}" ]] && ! cmp -s -- "${baseline_copy}" .secrets.baseline; then
    rewritten=1
  fi

  if [[ "${status}" -eq 0 && "${rewritten}" -eq 0 ]]; then
    rm -f -- "${out_file}" "${err_file}" ${baseline_copy:+"${baseline_copy}"}
    return 0
  fi

  if [[ -s "${out_file}" || -s "${err_file}" ]]; then
    echo "    detect-secrets-hook said:"
    sed -e 's/^/      /' -- "${out_file}" "${err_file}"
  else
    echo "    detect-secrets-hook produced no output."
  fi
  rm -f -- "${out_file}" "${err_file}" ${baseline_copy:+"${baseline_copy}"}

  if [[ "${rewritten}" -eq 1 ]]; then
    FAILURE_REASON="detect-secrets REWROTE .secrets.baseline; the bytes on disk changed, which was verified by comparison and is NOT a secret finding. Restore it with 'git checkout -- .secrets.baseline', then refresh with 'python scripts/refresh_secrets_baseline.py', which keeps keys posix"
    return 1
  fi

  # Deliberately NOT a cause. This hook returns 1 for a secret outside the
  # baseline AND for an unstaged baseline AND for other argument errors, and
  # this script cannot tell those apart, so it does not pretend to. The hook's
  # own output, printed immediately above, is the authoritative statement.
  FAILURE_REASON="detect-secrets-hook exited ${status}. Its own output above is the reason; this script does not infer one from the exit code"
  return 1
}

check --tool "${RUFF}" "Ruff lint" "${RUFF}" check .
check --tool "${RUFF}" "Ruff format check" "${RUFF}" format --check .
check --tool "${PYTEST}" "Pytest" "${PYTEST}" -q
check --tool npm --path node_modules "Prettier check" npm run --silent format:check
check --tool npm --path node_modules "ESLint" npm run --silent lint
check --tool "${PYTHON}" "Secrets baseline shape" "${PYTHON}" scripts/check_secrets_baseline.py
# After the shape check on purpose: detect-secrets-hook rewrites the baseline when
# it updates it, so scanning first would repair the file and hide what was committed.
check --tool "${DETECT_HOOK}" "Secret scan" secret_scan
check --tool "${PYTHON}" "URL host allowlist" "${PYTHON}" scripts/check_urls.py
check --tool "${PIP_AUDIT}" "Python dependency audit" "${PIP_AUDIT}" -r requirements-dev.txt

if [[ "${RUN_NODE_AUDIT}" -eq 1 ]]; then
  check --tool npm --path node_modules "Node dependency audit" npm audit --omit=dev --audit-level=high
fi

echo ""
if [[ "${#FAILURES[@]}" -gt 0 ]]; then
  # Each line carries the reason established for that check. The old summary
  # named only the checks, so a reader had to scroll back for the reason and in
  # practice supplied one from imagination instead.
  echo "CHECKS FAILED:"
  for failure in "${FAILURES[@]}"; do
    echo "  - ${failure}"
  done
  exit 1
fi
echo "ALL CHECKS PASSED"
exit 0
