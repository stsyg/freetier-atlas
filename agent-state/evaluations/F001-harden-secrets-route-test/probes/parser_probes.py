"""Parser-era probes for PR #69: anchors/aliases, fail-closed, diagnostics.

Written BEFORE the parser head landed, from predictions. Point it at a worktree:

    python parser_probes.py <worktree>

RULES is re-declared locally and MUST be re-read from the PR's ROUTE_RULES
before any result here is trusted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

WT = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "source_scan", WT / "tests" / "support" / "source_scan.py"
)
ss = importlib.util.module_from_spec(spec)
sys.modules["source_scan"] = ss
spec.loader.exec_module(ss)

VAL = "check_secrets_baseline"
CI = ".github/workflows/ci.yml"
PC = ".pre-commit-config.yaml"
INVOKE = f"python scripts/{VAL}.py"

# !!! RE-READ FROM ROUTE_RULES WHEN THE HEAD MOVES !!!
CI_STEP_RUN = (("jobs", "*", "steps[]", "run"), True)
PRECOMMIT_ENTRY = (("repos[]", "hooks[]", "entry"), False)
PRECOMMIT_ARGS = (("repos[]", "hooks[]", "args[]"), False)

RULES = {
    CI: (None, (CI_STEP_RUN,)),
    PC: (None, (PRECOMMIT_ENTRY, PRECOMMIT_ARGS)),
}


def judge(route: str, text: str, timeout_note: str = "") -> tuple[str, str]:
    """Return (verdict, detail). Never raises: a crash is itself a result."""
    contexts, keys = RULES[route]
    try:
        problems = ss.invocation_problems(route, text, VAL, contexts, keys)
    except RecursionError:
        return "CRASH", "RecursionError (recursive alias not defended)"
    except Exception as exc:  # noqa: BLE001
        return "CRASH", f"{type(exc).__name__}: {str(exc)[:160]}"
    if problems:
        return "REJECT", problems[0]
    return "ACCEPT", ""


def show(kind: str, label: str, route: str, want: str, text: str) -> None:
    verdict, detail = judge(route, text)
    ok = "   " if verdict == want else ">>>"
    print(f"{ok} [{kind}] {label:<46} want={want:<7} got={verdict}")
    if detail:
        # Does the message still carry a line number? (diagnostics regression)
        has_line = any(tok in detail for tok in ("line ", "line(s)"))
        print(f"        detail(line-ref={has_line}): {detail[:150]}")


print("=" * 78)
print("A. ANCHORS / ALIASES  — the composer resolves aliases but NOT '<<' merge")
print("=" * 78)

# A1: anchor defined at an INERT path, aliased INTO an executed path.
# The tool resolves the alias, so the validator really does run -> ACCEPT is correct.
show(
    "A1",
    "alias from inert anchor into entry:",
    PC,
    "ACCEPT",
    "x-defs:\n"
    f"  cmd: &cmd {INVOKE}\n"
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n        entry: *cmd\n        language: system\n",
)

# A2: anchor defined and NEVER aliased, at an inert path -> must REJECT.
show(
    "A2",
    "anchor never aliased, inert path",
    PC,
    "REJECT",
    "x-defs:\n"
    f"  cmd: &cmd {INVOKE}\n"
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n        entry: python other.py\n        language: system\n",
)

# A3: THE PREDICTION. '<<:' merge key. pre-commit uses PyYAML, which DOES honour
# the merge. compose_all() does NOT resolve it, so a compose-based path model
# should see key '<<' and reject a hook that legitimately runs the validator.
show(
    "A3",
    "'<<:' merge key into a hook  [PREDICT FP]",
    PC,
    "ACCEPT",
    "x-defs:\n"
    "  base: &base\n"
    f"    entry: {INVOKE}\n"
    "    language: system\n"
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n        <<: *base\n",
)

# A4: alias used for the whole hook mapping (no merge key).
show(
    "A4",
    "alias of a whole hook mapping",
    PC,
    "ACCEPT",
    "x-defs:\n"
    "  base: &base\n"
    "    id: sb\n"
    f"    entry: {INVOKE}\n"
    "    language: system\n"
    "repos:\n  - repo: local\n    hooks:\n      - *base\n",
)

# A5: recursive alias WITH the needle present. Must not hang or blow the stack.
show(
    "A5",
    "recursive alias + needle (no crash/hang)",
    PC,
    "ACCEPT",
    "repos: &r\n  - repo: local\n    hooks:\n      - id: sb\n        self: *r\n"
    f"        entry: {INVOKE}\n",
)

# A6: alias fan-out (mild billion-laughs shape) WITH the needle. Must terminate.
lol = "a0: &a0 [x, x, x, x]\n"
for i in range(1, 7):
    lol += f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}, *a{i - 1}, *a{i - 1}]\n"
lol += (
    "repos:\n  - repo: local\n    hooks:\n      - id: sb\n"
    f"        big: *a6\n        entry: {INVOKE}\n"
)
show("A6", "alias fan-out + needle must terminate", PC, "ACCEPT", lol)

print()
print("=" * 78)
print("B. FAIL-CLOSED ON UNPARSEABLE YAML — reject with a message, never pass")
print("=" * 78)

show(
    "B1",
    "tab indentation + needle at entry:",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: sb\n\t        entry: {INVOKE}\n",
)
show(
    "B2",
    "unclosed quote + needle at entry:",
    PC,
    "REJECT",
    f'repos:\n  - repo: local\n    hooks:\n      - id: "sb\n        entry: {INVOKE}\n',
)
show(
    "B3",
    "structurally invalid + needle at entry:",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n   :::\n    hooks:\n      - id: sb\n        entry: {INVOKE}\n",
)
show(
    "B4a",
    "dup key, validator OVERRIDDEN [PREDICT FN]",
    PC,
    "REJECT",
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    f"        entry: {INVOKE}\n"
    "        entry: python other.py\n",
)
show(
    "B4b",
    "dup key, validator WINS (last)",
    PC,
    "ACCEPT",
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    "        entry: python other.py\n"
    f"        entry: {INVOKE}\n",
)
show(
    "B6",
    "alias used BEFORE its anchor (fail closed)",
    PC,
    "REJECT",
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n        entry: *cmd\n"
    "x-defs:\n"
    f"  cmd: &cmd {INVOKE}\n",
)
show(
    "B7",
    "nested alias chain into entry:",
    PC,
    "ACCEPT",
    "x-defs:\n"
    f"  inner: &inner {INVOKE}\n"
    "  outer: &outer *inner\n"
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n        entry: *outer\n        language: system\n",
)
show(
    "B8",
    "anchor at inert key, aliased into run:",
    CI,
    "ACCEPT",
    "x-templates:\n"
    f"  secrets: &secrets {INVOKE}\n"
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Validate\n        run: *secrets\n",
)
show(
    "B9",
    "anchor aliased only into env: (decoy)",
    CI,
    "REJECT",
    "x-templates:\n"
    f"  secrets: &secrets {INVOKE}\n"
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Validate\n        env:\n          FOO: *secrets\n"
    "        run: echo hi\n",
)
show(
    "B10",
    "flow mapping hook  - {id: s, entry: ...}",
    PC,
    "ACCEPT",
    f"repos:\n  - repo: local\n    hooks:\n      - {{id: sb, entry: {INVOKE}, language: system}}\n",
)
show(
    "B11",
    "flow mapping step  - {name: s, run: ...}",
    CI,
    "ACCEPT",
    f"jobs:\n  lint:\n    steps:\n      - {{name: Validate, run: {INVOKE}}}\n",
)
show(
    "B5",
    "unparseable BUT needle present",
    PC,
    "REJECT",
    f"repos:\n  ::: bad\n  entry: {INVOKE}\n   - - -\n",
)

print()
print("=" * 78)
print("C. SHELL HALF STILL RECEIVES THE run: BODY (parser must not steal it)")
print("=" * 78)
for label, want, text in [
    (
        "heredoc burial inside run: block",
        "REJECT",
        "jobs:\n  lint:\n    steps:\n      - run: |\n"
        "          cat <<'EOF'\n"
        f"          {INVOKE}\n"
        "          EOF\n",
    ),
    (
        "'#' comment inside run: block",
        "REJECT",
        f"jobs:\n  lint:\n    steps:\n      - run: |\n          # {INVOKE}\n          true\n",
    ),
    (
        "top-level exit before invocation",
        "REJECT",
        f"jobs:\n  lint:\n    steps:\n      - run: |\n          exit 0\n          {INVOKE}\n",
    ),
    (
        "plain invocation in run: block",
        "ACCEPT",
        f"jobs:\n  lint:\n    steps:\n      - run: |\n          {INVOKE}\n",
    ),
]:
    show("C", label, CI, want, text)

print()
print("=" * 78)
print("D. SHIPPED FILES + Q6 EXPANSIONS (regression control)")
print("=" * 78)
for rel, expansion in ((CI, "${#files[@]}"), ("scripts/check.sh", "${#FAILURES[@]}")):
    text = (WT / rel).read_text(encoding="utf-8")
    try:
        surv = expansion in ss.executable_text(ss.scan(rel, text))
    except Exception as exc:  # noqa: BLE001
        surv = f"CRASH {type(exc).__name__}"
    print(f"    {rel:<28} {expansion:<18} survives scan = {surv}")
for rel in (CI, PC):
    v, d = judge(rel, (WT / rel).read_text(encoding="utf-8"))
    print(f"    shipped {rel:<28} -> {v} {d[:80]}")
