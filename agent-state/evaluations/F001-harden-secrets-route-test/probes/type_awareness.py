"""Is the walk TYPE-aware at every segment, or only at the leaf?

The orchestrator found `run:` accepting a SEQUENCE and diagnosed it as "args is
list-valued by schema; run and entry are scalar-valued, and the rule does not
record that distinction". That is right, and this asks whether the gap is
confined to terminal values or is general: INTERMEDIATE segments have schema
types too. `steps` must be a sequence; `repos` and `hooks` must be sequences;
`jobs` must be a mapping. `_select` traverses any list transparently and
descends any dict, so it can satisfy a pattern through a node whose type the
tool would reject.

Every ACCEPT below is a config the real tool refuses to run.
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
CI_RUN = (("jobs", "*", "steps[]", "run"), True)
PC_ENTRY = (("repos[]", "hooks[]", "entry"), False)
PC_ARGS = (("repos[]", "hooks[]", "args[]"), False)
RULES = {CI: (None, (CI_RUN,)), PC: (None, (PC_ENTRY, PC_ARGS))}


def judge(route, text):
    contexts, paths = RULES[route]
    try:
        p = ss.invocation_problems(route, text, VAL, contexts, paths)
    except Exception as exc:  # noqa: BLE001
        return "CRASH", f"{type(exc).__name__}"
    return ("REJECT", p[0]) if p else ("ACCEPT", "")


CASES = [
    # (label, route, schema-correct?, want, text)
    (
        "CONTROL steps: is a list",
        CI,
        True,
        "ACCEPT",
        f"jobs:\n  a:\n    steps:\n      - run: {INVOKE}\n",
    ),
    ("steps: is a MAPPING", CI, False, "REJECT", f"jobs:\n  a:\n    steps:\n      run: {INVOKE}\n"),
    ("steps: is a SCALAR", CI, False, "REJECT", f"jobs:\n  a:\n    steps: {INVOKE}\n"),
    (
        "run: is a SEQUENCE",
        CI,
        False,
        "REJECT",
        f"jobs:\n  a:\n    steps:\n      - run:\n          - {INVOKE}\n",
    ),
    (
        "run: is a MAPPING",
        CI,
        False,
        "REJECT",
        f"jobs:\n  a:\n    steps:\n      - run:\n          cmd: {INVOKE}\n",
    ),
    (
        "jobs: is a LIST",
        CI,
        False,
        "REJECT",
        f"jobs:\n  - a:\n      steps:\n        - run: {INVOKE}\n",
    ),
    (
        "CONTROL hooks: is a list",
        PC,
        True,
        "ACCEPT",
        f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: {INVOKE}\n",
    ),
    (
        "hooks: is a MAPPING",
        PC,
        False,
        "REJECT",
        f"repos:\n  - repo: local\n    hooks:\n      entry: {INVOKE}\n",
    ),
    (
        "repos: is a MAPPING",
        PC,
        False,
        "REJECT",
        f"repos:\n  local:\n    hooks:\n      - id: s\n        entry: {INVOKE}\n",
    ),
    (
        "entry: is a SEQUENCE",
        PC,
        False,
        "REJECT",
        f"repos:\n  - repo: local\n    hooks:\n      - id: s\n"
        f"        entry:\n          - {INVOKE}\n",
    ),
    (
        "CONTROL args: is a list",
        PC,
        True,
        "ACCEPT",
        f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
        f"        args:\n          - {INVOKE}\n",
    ),
    (
        "args: is a SCALAR",
        PC,
        False,
        "REJECT",
        f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
        f"        args: {INVOKE}\n",
    ),
    (
        "args: nested list-of-lists",
        PC,
        False,
        "REJECT",
        f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: python\n"
        f"        args:\n          - - {INVOKE}\n",
    ),
]

width = max(len(c[0]) for c in CASES) + 2
bad = []
print("=" * 78)
print("TYPE AWARENESS AT EVERY SCHEMA SEGMENT")
print("=" * 78)
for label, route, correct, want, text in CASES:
    verdict, _ = judge(route, text)
    ok = verdict == want
    tag = "schema-OK " if correct else "schema-BAD"
    print(f"{'   ' if ok else '>>>'} {label:<{width}} {tag} want={want:<7} got={verdict}")
    if not ok:
        bad.append(label)

print("\nmismatches:", len(bad))
for b in bad:
    print("  *", b)

print()
print("=" * 78)
print("DIAGNOSTIC TEXT — is a rejection's REASON accurate?")
print("=" * 78)
samples = [
    (
        "duplicate key, validator OVERRIDDEN",
        PC,
        f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        entry: {INVOKE}\n"
        "        entry: python other.py\n",
    ),
    (
        "needle only under name:",
        PC,
        f"repos:\n  - repo: local\n    hooks:\n      - id: s\n        name: {INVOKE}\n"
        "        entry: python other.py\n",
    ),
    (
        "unparseable + needle present",
        PC,
        f"repos:\n  - repo: local\n   :::\n    hooks:\n      - id: s\n        entry: {INVOKE}\n",
    ),
    ("commented out in the real ci.yml", CI, None),
]
for label, route, text in samples:
    if text is None:
        raw = (WT / route).read_text(encoding="utf-8")
        text = "".join(
            (ln[: len(ln) - len(ln.lstrip())] + "# " + ln.lstrip()) if VAL in ln else ln
            for ln in raw.splitlines(keepends=True)
        )
    verdict, msg = judge(route, text)
    print(f"\n  [{label}] -> {verdict}")
    print(f"    {msg[:300] if msg else '(accepted)'}")
    if verdict == "REJECT" and VAL in text:
        misleading = "absent entirely" in msg
        print(f"    needle IS textually present; message says 'absent entirely': {misleading}")
