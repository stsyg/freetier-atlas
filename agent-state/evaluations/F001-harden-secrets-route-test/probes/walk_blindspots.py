"""Walk blind spots, unexpected-but-valid shapes, and diagnostic quality.

Targets 2, 3 and 5 of the orchestrator's parser-head brief. Every case is
written to FALSIFY the claim that a schema walk is safe, not to confirm it.

A scanner and a walk fail differently. A scanner over-reaches: it finds a key
anywhere and believes it. A walk under-reaches: when the shape is not the one it
expects it silently visits nothing, returns "no invocation found", and produces
a REJECT that is indistinguishable from a genuine one. The second failure is the
more dangerous of the two here, because REJECT is the safe-looking answer and
nobody investigates a guard that is merely strict.

So for every case this records three things, not one:
  * the verdict (ACCEPT / REJECT / CRASH),
  * whether a REJECT came with an ACTIONABLE message, and
  * whether the message names a LOCATION.

"Rejected for the wrong reason with a misleading message" is a finding.

Usage:  python walk_blindspots.py <worktree>
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

# !!! RE-READ FROM THE PR's ROUTE_RULES BEFORE TRUSTING ANY RESULT !!!
CI_STEP_RUN = (("jobs", "*", "steps[]", "run"), True)
PRECOMMIT_ENTRY = (("repos[]", "hooks[]", "entry"), False)
PRECOMMIT_ARGS = (("repos[]", "hooks[]", "args[]"), False)

RULES = {
    CI: (None, (CI_STEP_RUN,)),
    PC: (None, (PRECOMMIT_ENTRY, PRECOMMIT_ARGS)),
}


def judge(route: str, text: str) -> tuple[str, str]:
    contexts, keys = RULES[route]
    try:
        problems = ss.invocation_problems(route, text, VAL, contexts, keys)
    except RecursionError:
        return "CRASH", "RecursionError"
    except Exception as exc:  # noqa: BLE001
        return "CRASH", f"{type(exc).__name__}: {str(exc)[:200]}"
    return ("REJECT", problems[0]) if problems else ("ACCEPT", "")


def grade(route: str, msg: str) -> str:
    """Is a rejection message actionable, and does it locate anything?"""
    if not msg:
        return ""
    names_file = route in msg
    located = any(t in msg for t in ("line ", "line(s)", "key=", "key ", "path"))
    raw = msg.strip().startswith(("Traceback", "yaml.", "ScannerError", "ParserError"))
    bits = []
    bits.append("file" if names_file else "NO-FILE")
    bits.append("loc" if located else "NO-LOC")
    if raw:
        bits.append("RAW-EXC")
    return "/".join(bits)


CASES: list[tuple[str, str, str, str, str]] = []


def add(group: str, label: str, route: str, want: str, text: str) -> None:
    CASES.append((group, label, route, want, text))


# ---------------------------------------------------------- 2. walk blind spots
add("WALK", "steps: null", CI, "REJECT", "jobs:\n  lint:\n    runs-on: x\n    steps:\n")
add("WALK", "steps: absent entirely", CI, "REJECT", "jobs:\n  lint:\n    runs-on: x\n")
add("WALK", "steps: empty list", CI, "REJECT", "jobs:\n  lint:\n    steps: []\n")
add("WALK", "jobs: null", CI, "REJECT", "name: CI\njobs:\n")
add("WALK", "jobs: absent (workflow_call only)", CI, "REJECT", "on:\n  workflow_call:\n")
add(
    "WALK",
    "reusable workflow job (uses:, no steps)",
    CI,
    "REJECT",
    "jobs:\n  lint:\n    uses: org/repo/.github/workflows/w.yml@main\n",
)
add(
    "WALK",
    "job-level defaults.run carries needle",
    CI,
    "REJECT",
    "jobs:\n  lint:\n    defaults:\n      run:\n"
    f"        shell: {INVOKE}\n"
    "    steps:\n      - run: echo hi\n",
)
# These MUST still be accepted: a walk that misses them under-reaches.
add(
    "WALK",
    "matrix job, needle in a real step",
    CI,
    "ACCEPT",
    "jobs:\n  lint:\n    strategy:\n      matrix:\n        py: ['3.12','3.13']\n"
    "    steps:\n      - name: Validate\n"
    f"        run: {INVOKE}\n",
)
add(
    "WALK",
    "needle in the SECOND job",
    CI,
    "ACCEPT",
    "jobs:\n  build:\n    steps:\n      - run: echo hi\n"
    "  lint:\n    steps:\n"
    f"      - run: {INVOKE}\n",
)
add(
    "WALK",
    "null step alongside a real one",
    CI,
    "ACCEPT",
    f"jobs:\n  lint:\n    steps:\n      -\n      - run: {INVOKE}\n",
)
add(
    "WALK",
    "step is a string, not a mapping",
    CI,
    "ACCEPT",
    f"jobs:\n  lint:\n    steps:\n      - just-a-string\n      - run: {INVOKE}\n",
)
add("WALK", "repos: empty list", PC, "REJECT", "repos: []\n")
add("WALK", "repos: null", PC, "REJECT", "repos:\n")
add("WALK", "hooks: null", PC, "REJECT", "repos:\n  - repo: local\n    hooks:\n")
add(
    "WALK",
    "args: present but null",
    PC,
    "REJECT",
    "repos:\n  - repo: local\n    hooks:\n      - id: sb\n        entry: python\n        args:\n",
)
add(
    "WALK",
    "needle in the SECOND repo block",
    PC,
    "ACCEPT",
    "repos:\n  - repo: https://example.com/x\n    rev: v1\n    hooks:\n      - id: a\n"
    "  - repo: local\n    hooks:\n"
    f"      - id: sb\n        entry: {INVOKE}\n        language: system\n",
)

# ------------------------------------------- 3. valid YAML, unexpected shape
add("SHAPE", "pre-commit is a top-level LIST", PC, "REJECT", f"- entry: {INVOKE}\n")
add("SHAPE", "workflow is a bare string", CI, "REJECT", f"{INVOKE}\n")
add("SHAPE", "empty document", CI, "REJECT", "")
add("SHAPE", "document is just a comment", CI, "REJECT", f"# {INVOKE}\n")
add("SHAPE", "explicit YAML null document", CI, "REJECT", "~\n")
add(
    "SHAPE",
    "multi-document, needle in doc 2",
    CI,
    "ACCEPT",
    "jobs:\n  a:\n    steps:\n      - run: echo hi\n"
    "---\n"
    "jobs:\n  b:\n    steps:\n"
    f"      - run: {INVOKE}\n",
)
add(
    "SHAPE",
    "needle at top level under a stray run:",
    CI,
    "REJECT",
    f"run: {INVOKE}\njobs:\n  lint:\n    steps:\n      - run: echo hi\n",
)

# ------------------------------------------------------- fail-closed, needled
add(
    "FAILCLOSED",
    "tab indentation + needle at entry:",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n    hooks:\n      - id: sb\n\t        entry: {INVOKE}\n",
)
add(
    "FAILCLOSED",
    "unclosed quote + needle at entry:",
    PC,
    "REJECT",
    f'repos:\n  - repo: local\n    hooks:\n      - id: "sb\n        entry: {INVOKE}\n',
)
add(
    "FAILCLOSED",
    "invalid structure + needle at entry:",
    PC,
    "REJECT",
    f"repos:\n  - repo: local\n   :::\n    hooks:\n      - id: sb\n        entry: {INVOKE}\n",
)
add(
    "FAILCLOSED",
    "duplicate key, validator OVERRIDDEN",
    PC,
    "REJECT",
    "repos:\n  - repo: local\n    hooks:\n      - id: sb\n"
    f"        entry: {INVOKE}\n        entry: python other.py\n",
)


def main() -> int:
    width = max(len(c[1]) for c in CASES) + 2
    findings: list[str] = []
    for group in ("WALK", "SHAPE", "FAILCLOSED"):
        print(f"\n===== {group} " + "=" * (56 - len(group)))
        for g, label, route, want, text in CASES:
            if g != group:
                continue
            verdict, msg = judge(route, text)
            ok = verdict == want
            note = grade(route, msg)
            print(
                f"{'   ' if ok else '>>>'} {label:<{width}} want={want:<7} got={verdict:<7} {note}"
            )
            if verdict == "CRASH":
                findings.append(f"CRASH: {label} -> {msg}")
            elif not ok and want == "ACCEPT":
                findings.append(f"WALK UNDER-REACHES (legit shape rejected): {label}")
            elif not ok and want == "REJECT":
                findings.append(f"ACCEPTED AN INERT/BROKEN SHAPE: {label}")
            if verdict == "REJECT" and "NO-LOC" in note:
                findings.append(f"DIAGNOSTIC: no location in message for {label}")
            if verdict == "REJECT" and "RAW-EXC" in note:
                findings.append(f"DIAGNOSTIC: raw exception leaked for {label}")
            if msg:
                print(f"{'':<{width + 4}}  {msg[:140]}")

    print("\n===================== FINDINGS =====================")
    print("none" if not findings else "\n".join(" * " + f for f in findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
