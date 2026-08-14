"""Level-2 evaluation harness for PR #69 (secrets-baseline route guard).

Independent of the test module under review: loads ``source_scan`` by path and
re-declares ROUTE_RULES locally, so a change to the PR's table does not silently
change what this harness believes the contract is.

Usage:  python eval_harness.py <worktree>

Categories
----------
CAUGHT      the PR claims this is caught -> must REJECT. A PASS is a false claim.
LISTED      the PR claims this is knowingly NOT caught -> PASS is expected and
            is NOT a finding. A REJECT is a pleasant surprise, not a defect.
HUNT        neither caught nor listed. A PASS here is a dispositive finding.
FP          correct work -> must PASS. A REJECT is a false positive.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

WORKTREE = Path(sys.argv[1])
SCAN_PATH = WORKTREE / "tests" / "support" / "source_scan.py"

spec = importlib.util.spec_from_file_location("source_scan", SCAN_PATH)
source_scan = importlib.util.module_from_spec(spec)
sys.modules["source_scan"] = source_scan  # dataclasses needs this BEFORE exec
spec.loader.exec_module(source_scan)

VAL = "check_secrets_baseline"
CI = ".github/workflows/ci.yml"
PC = ".pre-commit-config.yaml"
PS = "scripts/check.ps1"
SH = "scripts/check.sh"

CI_STEP_RUN = (("jobs", "*", "steps[]", "run"), True)
PRECOMMIT_ENTRY = (("repos[]", "hooks[]", "entry"), False)
PRECOMMIT_ARGS = (("repos[]", "hooks[]", "args[]"), False)

RULES = {
    CI: (None, (CI_STEP_RUN,)),
    PC: (None, (PRECOMMIT_ENTRY, PRECOMMIT_ARGS)),
    PS: (frozenset({"powershell"}), None),
    SH: (frozenset({"shell"}), None),
}

ROUTES = {p: (WORKTREE / p).read_text(encoding="utf-8") for p in RULES}


def judge(route: str, text: str) -> tuple[bool, str]:
    contexts, keys = RULES[route]
    problems = source_scan.invocation_problems(route, text, VAL, contexts, keys)
    return (problems == []), (problems[0] if problems else "")


# ---------------------------------------------------------------- mutators


def comment_out(text: str, opener: str = "# ") -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            s = line.lstrip()
            out.append(line[: len(line) - len(s)] + opener + s)
        else:
            out.append(line)
    return "".join(out)


def delete_invocation(text: str) -> str:
    return "".join(ln for ln in text.splitlines(keepends=True) if VAL not in ln)


def ps_block_comment(text: str) -> str:
    return "".join(f"<#{ln}#>\n" if VAL in ln else ln for ln in text.splitlines(keepends=True))


def ps_nested_block_comment(text: str) -> str:
    return "".join(
        f"<# outer <# inner #> {ln}#>\n" if VAL in ln else ln
        for ln in text.splitlines(keepends=True)
    )


def early_exit(text: str, stmt: str = "exit 0") -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}{stmt}\n")
        out.append(line)
    return "".join(out)


def conditional_exit(text: str) -> str:
    return early_exit(text, "true && exit 0")


def wrap_if_false(text: str, lang: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            indent = line[: len(line) - len(line.lstrip())]
            if lang == "sh":
                out.append(f"{indent}if false; then\n")
                out.append(line)
                out.append(f"{indent}fi\n")
            else:
                out.append(f"{indent}if ($false) {{\n")
                out.append(line)
                out.append(f"{indent}}}\n")
        else:
            out.append(line)
    return "".join(out)


def dead_function(text: str, lang: str) -> str:
    """Bury the invocation in a function that is defined but never called."""
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            indent = line[: len(line) - len(line.lstrip())]
            if lang == "sh":
                out.append(f"{indent}__never_called() {{\n")
                out.append(line)
                out.append(f"{indent}}}\n")
            else:
                out.append(f"{indent}function __NeverCalled {{\n")
                out.append(line)
                out.append(f"{indent}}}\n")
        else:
            out.append(line)
    return "".join(out)


def into_heredoc(text: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}cat <<'EOF'\n")
            out.append(line)
            out.append(f"{indent}EOF\n")
        else:
            out.append(line)
    return "".join(out)


def into_herestring(text: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            out.append("$doc = @'\n")
            out.append(line)
            out.append("'@\n")
        else:
            out.append(line)
    return "".join(out)


def into_echo_string(text: str, lang: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}echo "would run {VAL}.py"\n')
        else:
            out.append(line)
    return "".join(out)


def and_chain_false(text: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + "false && " + line.lstrip())
        else:
            out.append(line)
    return "".join(out)


def trailing_ws_comment(text: str) -> str:
    """Comment out with a tab between '#' and the code, plus trailing spaces."""
    out = []
    for line in text.splitlines(keepends=True):
        if VAL in line:
            s = line.lstrip()
            out.append(line[: len(line) - len(s)] + "#\t" + s.rstrip("\n") + "   \n")
        else:
            out.append(line)
    return "".join(out)


def to_crlf(text: str) -> str:
    return text.replace("\n", "\r\n")


CASES: list[tuple[str, str, str, bool, str]] = []


def add(cat: str, label: str, route: str, must_pass: bool, text: str) -> None:
    CASES.append((cat, label, route, must_pass, text))


# ------------------------------------------------- Q1: claimed CAUGHT
for r in RULES:
    add("CAUGHT", f"comment out '# '        {r}", r, False, comment_out(ROUTES[r]))
    add("CAUGHT", f"delete invocation       {r}", r, False, delete_invocation(ROUTES[r]))
add("CAUGHT", f"PS block comment <# #>  {PS}", PS, False, ps_block_comment(ROUTES[PS]))
add("CAUGHT", f"PS NESTED block comment {PS}", PS, False, ps_nested_block_comment(ROUTES[PS]))
add("CAUGHT", f"heredoc burial          {SH}", SH, False, into_heredoc(ROUTES[SH]))
add("CAUGHT", f"heredoc burial          {CI}", CI, False, into_heredoc(ROUTES[CI]))
add("CAUGHT", f"here-string burial      {PS}", PS, False, into_herestring(ROUTES[PS]))
for r, stmt in ((SH, "exit 0"), (PS, "exit 0"), (CI, "exit 0")):
    add("CAUGHT", f"uncond top-level exit   {r}", r, False, early_exit(ROUTES[r], stmt))

# ------------------------------------------------- Q2: LISTED not-caught
add("LISTED", f"if false wrapper        {SH}", SH, True, wrap_if_false(ROUTES[SH], "sh"))
add("LISTED", f"if (false) wrapper      {PS}", PS, True, wrap_if_false(ROUTES[PS], "ps"))
add("LISTED", f"conditional early exit  {SH}", SH, True, conditional_exit(ROUTES[SH]))
add("LISTED", f"display string only     {SH}", SH, True, into_echo_string(ROUTES[SH], "sh"))
# check.sh is `set -uo pipefail` with NO -e, so `false && cmd` silently skips and
# the script still exits 0. Counted as a spelling of the listed always-false
# guard rather than a new class, to avoid inflating the finding count.
add("LISTED", f"false && chain          {SH}", SH, True, and_chain_false(ROUTES[SH]))
# Burying the call site in an uncalled function is a REACHABILITY question, and
# the PR explicitly bounds reachability to unconditional top-level exits.
add("LISTED", f"dead never-called func  {SH}", SH, True, dead_function(ROUTES[SH], "sh"))
add("LISTED", f"dead never-called func  {PS}", PS, True, dead_function(ROUTES[PS], "ps"))
# The PR lists a workflow-level `if:` that skips the job; step-level is the same family.
add(
    "LISTED",
    "step-level 'if: false'",
    CI,
    True,
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Validate\n"
    "        if: false\n"
    "        run: |\n"
    f"          python scripts/{VAL}.py\n",
)

# ------------------------------------------------- Q2: HUNT (unlisted)
add("HUNT", f"'#\\t' comment + trail ws {SH}", SH, False, trailing_ws_comment(ROUTES[SH]))
add("HUNT", f"'#\\t' comment + trail ws {PS}", PS, False, trailing_ws_comment(ROUTES[PS]))
# CRLF is a FALSE-POSITIVE probe, not a bypass probe: a CRLF file that still
# invokes the validator must be ACCEPTED.
add("FP", f"CRLF line endings       {CI}", CI, True, to_crlf(ROUTES[CI]))
add("FP", f"CRLF line endings       {SH}", SH, True, to_crlf(ROUTES[SH]))
add("FP", f"CRLF line endings       {PS}", PS, True, to_crlf(ROUTES[PS]))

# `with: run: |` - an INPUT to a third-party action, never executed as a step.
add(
    "HUNT",
    "inert 'with: run: |' input",
    CI,
    True,
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Example only\n"
    "        uses: third/party@v1\n"
    "        with:\n"
    "          run: |\n"
    f"            python scripts/{VAL}.py\n",
)
# YAML anchor never aliased.
add(
    "HUNT",
    "unused YAML anchor &tmpl",
    CI,
    True,
    "x-templates:\n"
    "  disabled: &tmpl\n"
    "    run: |\n"
    f"      python scripts/{VAL}.py\n"
    "jobs:\n  lint:\n    steps:\n      - run: echo hi\n",
)
# Step-level if: false is now classified LISTED (same family as the documented
# workflow-level `if:`). Renaming to a decoy that merely CONTAINS the needle is
# NOT a silent bypass: `python scripts/..._DISABLED.py` fails loudly at runtime,
# so it is recorded as INFO rather than counted as a finding.
add(
    "INFO",
    "needle-containing decoy name",
    SH,
    True,
    ROUTES[SH].replace(f"{VAL}.py", f"{VAL}_DISABLED.py"),
)

# ------------------------------------------------- Q3: FP controls (must PASS)
for r in RULES:
    add("FP", f"UNTOUCHED (positive ctl) {r}", r, True, ROUTES[r])

add(
    "FP",
    "ci.yml inline run:",
    CI,
    True,
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Validate secrets baseline\n"
    f"        run: python scripts/{VAL}.py --require-reference\n",
)
add(
    "FP",
    "ci.yml folded run: >",
    CI,
    True,
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Validate secrets baseline\n"
    "        run: >\n"
    f"          python scripts/{VAL}.py --require-reference\n",
)
add(
    "FP",
    "pre-commit flow entry:",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    f"        entry: python scripts/{VAL}.py\n"
    "        language: system\n",
)
add(
    "FP",
    "pre-commit block-seq args:",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    "        entry: python\n"
    "        args:\n"
    f"          - scripts/{VAL}.py\n"
    "        language: system\n",
)
add(
    "FP",
    "pre-commit flow-seq args:[]",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    "        entry: python\n"
    f"        args: [scripts/{VAL}.py]\n"
    "        language: system\n",
)
add(
    "FP",
    "pre-commit block entry: |",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    "        entry: |\n"
    f"          python scripts/{VAL}.py\n"
    "        language: system\n",
)
add(
    "FP",
    "PS backtick continuation",
    PS,
    True,
    f"$python = 'python'\n& $python `\n    \"scripts/{VAL}.py\" `\n    --require-reference\n",
)
add(
    "FP",
    "PS quoted interpreter path",
    PS,
    True,
    f'& $python "scripts/{VAL}.py" --require-reference\n',
)
add(
    "FP",
    "PS re-indented in if block",
    PS,
    True,
    "if ($true) {\n"
    "        if ($true) {\n"
    f'                & $python "scripts/{VAL}.py"\n'
    "        }\n"
    "}\n",
)
add(
    "FP",
    "sh extracted function+call",
    SH,
    True,
    "run_secrets_check() {\n"
    f'  python "scripts/{VAL}.py" --require-reference\n'
    "}\n"
    "run_secrets_check\n",
)
add(
    "FP",
    "sh quoted path + ${#arr[@]}",
    SH,
    True,
    f'files=(a b)\necho "${{#files[@]}}"\npython "scripts/{VAL}.py" --require-reference\n',
)

# ------------------------------------------------- negative controls
add(
    "NEGCTL",
    "needle only under name: (CI)",
    CI,
    False,
    f"jobs:\n  lint:\n    steps:\n      - name: runs {VAL}.py\n        run: |\n          echo hi\n",
)
add(
    "NEGCTL",
    "needle only under name: (PC)",
    PC,
    False,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    f"        name: runs {VAL}.py\n"
    "        entry: python scripts/other.py\n",
)


def main() -> int:
    order = ["FP", "NEGCTL", "CAUGHT", "LISTED", "HUNT", "INFO"]
    width = max(len(lbl) for _, lbl, *_ in CASES) + 2
    findings: list[str] = []
    for cat in order:
        rows = [c for c in CASES if c[0] == cat]
        if not rows:
            continue
        print(f"\n===== {cat} " + "=" * (60 - len(cat)))
        for _, label, route, must_pass, text in rows:
            passed, why = judge(route, text)
            got = "PASS" if passed else "REJECT"
            want = "PASS" if must_pass else "REJECT"
            ok = passed == must_pass
            flag = "   " if ok else ">>>"
            print(f"{flag} {label:<{width}} want={want:<6} got={got}")
            if not ok:
                if cat == "FP":
                    findings.append(f"FALSE POSITIVE: {label} -> {why[:150]}")
                elif cat == "CAUGHT":
                    findings.append(f"FALSE CAUGHT-CLAIM: {label} (bypass works)")
                elif cat == "NEGCTL":
                    findings.append(f"NEGATIVE CONTROL BROKE: {label}")
                elif cat == "LISTED":
                    findings.append(f"(bonus) LISTED-but-actually-caught: {label}")
            if cat == "HUNT" and passed:
                findings.append(f"UNLISTED BYPASS: {label}")

    print("\n\n===================== FINDINGS =====================")
    if not findings:
        print("none")
    for f in findings:
        print(" * " + f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
