"""Arity probes: `_select` now distinguishes INTERMEDIATE from TERMINAL sequences.

New control flow is where regressions live, so this pins both directions of the
new rule and hunts the shape the fix most plausibly misses.

The fix reportedly makes a pattern carry `(path, is_shell_script, is_list_valued)`
and treats an intermediate sequence (`steps`, `repos`, `hooks`) as always
traversed, while a terminal sequence is accepted only where the schema says list
(`args`) and refused where it says scalar (`run`, `entry`).

THE PREDICTION THIS FILE EXISTS FOR: "intermediate sequences are ALWAYS
traversed" is itself type-blind. `repos` is a list *of mappings*, not a list of
lists. If traversal is unconditional recursion over any list, then
`repos: [[ {hooks: ...} ]]` is still reached, and the original transparent-
traversal defect survives the arity fix one level up. Same for `steps` and
`hooks`. Written before measuring.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_loader import load_module  # noqa: E402

WT = Path(sys.argv[1])
ss = load_module(WT / "tests" / "support" / "source_scan.py", "source_scan")

VAL = "check_secrets_baseline"
CI = ".github/workflows/ci.yml"
PC = ".pre-commit-config.yaml"
INVOKE = f"python scripts/{VAL}.py"


# !!! RE-READ ROUTE_RULES FROM THE HEAD. The tuple gained a third element on
# 05cb61e; this file tries the 3-tuple first and falls back to the 2-tuple so it
# can run against either shape and SAYS which one it used.
def build_rules():
    try:
        rules = {
            CI: (None, ((("jobs", "*", "steps[]", "run"), True),)),
            PC: (
                None,
                (
                    (("repos[]", "hooks[]", "entry"), False),
                    (("repos[]", "hooks[]", "args[]"), False),
                ),
            ),
        }
        ss.invocation_problems(CI, "jobs:\n", VAL, *rules[CI])
        return rules, "2-tuple with TYPED path segments (steps[], args[])"
    except Exception:
        rules = {
            CI: (None, ((("jobs", "*", "steps", "run"), True),)),
            PC: (
                None,
                ((("repos", "hooks", "entry"), False), (("repos", "hooks", "args"), False)),
            ),
        }
        return rules, "2-tuple (path, shell)"


RULES, SHAPE = build_rules()


def judge(route: str, text: str) -> tuple[str, str]:
    contexts, paths = RULES[route]
    try:
        p = ss.invocation_problems(route, text, VAL, contexts, paths)
    except RecursionError:
        return "CRASH", "RecursionError"
    except Exception as exc:  # noqa: BLE001
        return "CRASH", f"{type(exc).__name__}: {str(exc)[:150]}"
    return ("REJECT", p[0]) if p else ("ACCEPT", "")


C = []


def add(label, route, want, text, note=""):
    C.append((label, route, want, text, note))


# ---- terminal arity, the direction the fix targets
add(
    "CONTROL args: list (schema list)",
    PC,
    "ACCEPT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
    f"        args:\n          - {INVOKE}\n",
)
add(
    "args: scalar",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
    f"        args: {INVOKE}\n",
)
add(
    "args: list of lists",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
    f"        args:\n          - - {INVOKE}\n",
)
add(
    "args: list of lists of lists",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
    f"        args:\n          - - - {INVOKE}\n",
)
add(
    "args: list containing a MAPPING",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
    f"        args:\n          - k: {INVOKE}\n",
)
add("CONTROL run: scalar", CI, "ACCEPT", f"jobs:\n  a:\n    steps:\n      - run: {INVOKE}\n")
add("run: sequence", CI, "REJECT", f"jobs:\n  a:\n    steps:\n      - run:\n          - {INVOKE}\n")
add(
    "entry: sequence",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry:\n          - {INVOKE}\n",
)

# ---- THE PREDICTION: intermediate sequences are type-blind in the other direction
add(
    "repos: list OF LISTS",
    PC,
    "REJECT",
    f"repos:\n  - - repo: local\n      hooks:\n        - id: s\n          entry: {INVOKE}\n",
    "repos is a list of MAPPINGS; unconditional list recursion still reaches this",
)
add(
    "hooks: list OF LISTS",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - - id: s\n          entry: {INVOKE}\n",
    "hooks is a list of MAPPINGS",
)
add(
    "steps: list OF LISTS",
    CI,
    "REJECT",
    f"jobs:\n  a:\n    steps:\n      - - run: {INVOKE}\n",
    "steps is a list of MAPPINGS",
)

# ---- converse direction: sequence-typed key given a mapping
add("steps: is a MAPPING", CI, "REJECT", f"jobs:\n  a:\n    steps:\n      run: {INVOKE}\n")
add(
    "hooks: is a MAPPING",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      entry: {INVOKE}\n",
)
add("jobs: is a LIST", CI, "REJECT", f"jobs:\n  - a:\n      steps:\n        - run: {INVOKE}\n")
add(
    "repos: is a MAPPING",
    PC,
    "REJECT",
    f"repos:\n  local:\n    hooks:\n      - id: s\n        entry: {INVOKE}\n",
)
add("repos: is a SCALAR", PC, "REJECT", f"repos: {INVOKE}\n")
add("hooks: is a SCALAR", PC, "REJECT", f"repos:\n  - repo: local\n    hooks: {INVOKE}\n")

# ---- multi-document (Finding 2)
add(
    "pre-commit multi-doc, needle in doc 2",
    PC,
    "REJECT",
    "repos:\n  - repo: local\n    hooks:\n      - id: a\n        entry: python other.py\n"
    f"---\nrepos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: {INVOKE}\n",
)
add(
    "workflow multi-doc, needle in doc 2",
    CI,
    "REJECT",
    "jobs:\n  a:\n    steps:\n      - run: echo hi\n"
    f"---\njobs:\n  b:\n    steps:\n      - run: {INVOKE}\n",
)

# ---- regression controls for the 16 accept spellings most at risk from arity
add(
    "REGRESSION multi-item args",
    PC,
    "ACCEPT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
    f"        args:\n          - -X\n          - {INVOKE}\n",
)
add(
    "REGRESSION flow-seq args",
    PC,
    "ACCEPT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
    f'        args: ["{INVOKE}"]\n',
)
add(
    "REGRESSION flow-mapping hook",
    PC,
    "ACCEPT",
    f"repos:\n  - repo: local\n    hooks:\n      - {{id: s, entry: {INVOKE}}}\n",
)
add(
    "REGRESSION flow-mapping step",
    CI,
    "ACCEPT",
    f"jobs:\n  a:\n    steps:\n      - {{name: v, run: {INVOKE}}}\n",
)
add(
    "REGRESSION block scalar run",
    CI,
    "ACCEPT",
    f"jobs:\n  a:\n    steps:\n      - run: |\n          {INVOKE}\n",
)
add(
    "REGRESSION anchor aliases steps list",
    CI,
    "ACCEPT",
    f"x: &s\n  - run: {INVOKE}\njobs:\n  a:\n    steps: *s\n",
)
add(
    "REGRESSION alias into entry",
    PC,
    "ACCEPT",
    f"x: &c {INVOKE}\nrepos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: *c\n",
)
add(
    "NEGCTL needle only under name:",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        name: {INVOKE}\n"
    "        entry: python other.py\n",
)


def main() -> int:
    print(f"pattern tuple shape in use: {SHAPE}\n")
    w = max(len(c[0]) for c in C) + 2
    bad = []
    for label, route, want, text, note in C:
        v, msg = judge(route, text)
        ok = v == want
        print(f"{'   ' if ok else '>>>'} {label:<{w}} want={want:<7} got={v}")
        if not ok:
            if note:
                print(f"{'':<{w + 4}}  {note}")
            bad.append(f"{v} (wanted {want}): {label}")
    print("\n===================== ARITY FINDINGS =====================")
    print("none" if not bad else "\n".join(" * " + b for b in bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
