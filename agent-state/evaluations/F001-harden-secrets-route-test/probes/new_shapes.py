"""Shapes nobody has named yet, aimed at the PARSER's own new risk surface.

`ef5783e` was a hand-rolled line scanner; `07baaab` is `yaml.safe_load_all` plus
a schema walk. That trade removes a family of defects and BUYS a new one: a real
loader constructs real Python objects, and objects can be cyclic, enormous, or
of a type the walk does not expect. A walk also under-reaches silently - when
the shape is not the one it expects it visits nothing and returns "no
invocation", which is indistinguishable from a genuine rejection.

Each case says what SHOULD happen and why, so a surprise is legible.

Usage:  <venv-python> new_shapes.py <worktree>
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

CI_STEP_RUN = (("jobs", "*", "steps[]", "run"), True)
PRECOMMIT_ENTRY = (("repos[]", "hooks[]", "entry"), False)
PRECOMMIT_ARGS = (("repos[]", "hooks[]", "args[]"), False)
RULES = {CI: (None, (CI_STEP_RUN,)), PC: (None, (PRECOMMIT_ENTRY, PRECOMMIT_ARGS))}


def judge(route: str, text: str) -> tuple[str, str]:
    contexts, paths = RULES[route]
    try:
        problems = ss.invocation_problems(route, text, VAL, contexts, paths)
    except RecursionError:
        return "CRASH", "RecursionError - cyclic object graph walked without a guard"
    except MemoryError:
        return "CRASH", "MemoryError - alias expansion unbounded"
    except Exception as exc:  # noqa: BLE001
        return "CRASH", f"{type(exc).__name__}: {str(exc)[:170]}"
    return ("REJECT", problems[0]) if problems else ("ACCEPT", "")


CASES: list[tuple[str, str, str, str, str]] = []


def add(label: str, route: str, want: str, why: str, text: str) -> None:
    CASES.append((label, route, want, why, text))


# ---- H1  A cyclic object graph. safe_load DOES construct recursive objects.
add(
    "recursive alias, needle present",
    PC,
    "ACCEPT",
    "safe_load builds a self-referencing list; a naive recursive walk never returns",
    "repos: &r\n  - repo: local\n    hooks:\n      - id: s\n        loop: *r\n"
    f"        entry: {INVOKE}\n",
)
add(
    "recursive alias, needle ABSENT",
    PC,
    "REJECT",
    "same graph, nothing to find - must still terminate",
    "repos: &r\n  - repo: local\n    hooks:\n      - id: s\n        loop: *r\n"
    "        entry: python other.py\n",
)

# ---- H2  Multi-document. pre-commit loads ONE document; safe_load_all reads all.
add(
    "pre-commit multi-doc, needle in doc 2",
    PC,
    "REJECT",
    "pre-commit reads a single document, so doc 2 never executes - accepting is a FN",
    "repos:\n  - repo: local\n    hooks:\n      - id: a\n        entry: python other.py\n"
    "---\n"
    "repos:\n  - repo: local\n    hooks:\n"
    f"      - id: s\n        entry: {INVOKE}\n",
)
add(
    "workflow multi-doc, needle in doc 2",
    CI,
    "REJECT",
    "Actions reads a single document; doc 2 is not executed",
    "jobs:\n  a:\n    steps:\n      - run: echo hi\n"
    "---\n"
    "jobs:\n  b:\n    steps:\n"
    f"      - run: {INVOKE}\n",
)

# ---- H3  Alias fan-out. safe_load EXPANDS aliases, so this is exponential.
bomb = "a0: &a0 [x,x,x,x,x,x,x,x,x,x]\n"
for i in range(1, 6):
    prev = f"*a{i - 1}"
    bomb += f"a{i}: &a{i} [{','.join([prev] * 10)}]\n"
bomb += (
    "repos:\n  - repo: local\n    hooks:\n      - id: s\n"
    f"        big: *a5\n        entry: {INVOKE}\n"
)
add(
    "alias fan-out 10^5 nodes",
    PC,
    "ACCEPT",
    "safe_load expands aliases; must terminate in reasonable time and memory",
    bomb,
)

# ---- H4  run: whose value is not a string.
for label, spelling in (
    ("run: is a LIST", "        run:\n          - " + INVOKE + "\n"),
    ("run: is null", "        run:\n"),
    ("run: is an int", "        run: 42\n"),
):
    add(
        label,
        CI,
        "REJECT",
        "not a string the tool executes; walk must not crash on the type",
        "jobs:\n  lint:\n    steps:\n      - name: v\n" + spelling,
    )

# ---- H5  steps: as a MAPPING rather than a sequence.
add(
    "steps: is a mapping, not a list",
    CI,
    "REJECT",
    "invalid workflow schema; a walk that descends dicts blindly would accept it",
    f"jobs:\n  lint:\n    steps:\n      run: {INVOKE}\n",
)

# ---- H6  YAML 1.1 boolean coercion (the Norway problem) on a JOB ID.
add(
    "job id 'on' coerced to boolean True",
    CI,
    "ACCEPT",
    "PyYAML 1.1 turns bare on/no/yes into booleans; '*' must still match the key",
    f"jobs:\n  on:\n    steps:\n      - run: {INVOKE}\n",
)
add(
    "job id 'no' coerced to boolean False",
    CI,
    "ACCEPT",
    "same coercion, falsy key - a truthiness test on the key would drop it",
    f"jobs:\n  no:\n    steps:\n      - run: {INVOKE}\n",
)
add(
    'step key quoted "run" vs bare run',
    CI,
    "ACCEPT",
    "quoting must not change the parsed key",
    f'jobs:\n  lint:\n    steps:\n      - "run": {INVOKE}\n',
)

# ---- H7  Tags.
add(
    "explicit !!str tag on run:",
    CI,
    "ACCEPT",
    "!!str is a plain string after loading",
    f"jobs:\n  lint:\n    steps:\n      - run: !!str {INVOKE}\n",
)
add(
    "unsafe !!python tag",
    CI,
    "REJECT",
    "safe_load refuses the tag; must fail CLOSED with a message, not raise",
    "jobs:\n  lint:\n    steps:\n"
    "      - run: !!python/object/apply:os.system ['echo hi']\n"
    f"      - run: {INVOKE}\n",
)
add(
    "!!binary run: value",
    CI,
    "REJECT",
    "loads as bytes, not str - walk must skip rather than crash",
    "jobs:\n  lint:\n    steps:\n      - run: !!binary aGVsbG8=\n",
)

# ---- H8  Anchor aliasing a whole steps: sequence.
add(
    "anchor aliases a whole steps: list",
    CI,
    "ACCEPT",
    "the aliased sequence really is the job's steps and really runs",
    f"x-steps: &s\n  - run: {INVOKE}\njobs:\n  lint:\n    steps: *s\n",
)
add(
    "anchored steps: list never aliased",
    CI,
    "REJECT",
    "defined under an inert key and never used - must not be reachable",
    f"x-steps: &s\n  - run: {INVOKE}\njobs:\n  lint:\n    steps:\n      - run: echo hi\n",
)

# ---- H9  Deep nesting.
deep = "jobs:\n  lint:\n    steps:\n      - run: " + INVOKE + "\n"
for _ in range(60):
    deep = "wrapper:\n" + "".join("  " + ln + "\n" for ln in deep.splitlines())
add(
    "needle buried 60 levels under a wrapper",
    CI,
    "REJECT",
    "not at jobs.* - a path walk must not find it; a tree search would",
    deep,
)


def main() -> int:
    width = max(len(c[0]) for c in CASES) + 2
    findings: list[str] = []
    for label, route, want, why, text in CASES:
        verdict, msg = judge(route, text)
        ok = verdict == want
        print(f"{'   ' if ok else '>>>'} {label:<{width}} want={want:<7} got={verdict}")
        if not ok or verdict == "CRASH":
            print(f"{'':<{width + 4}}  why: {why}")
            if msg:
                print(f"{'':<{width + 4}}  msg: {msg[:150]}")
            findings.append(f"{verdict} (wanted {want}): {label} — {why}")
    print("\n===================== NEW-SHAPE FINDINGS =====================")
    print("none" if not findings else "\n".join(" * " + f for f in findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
