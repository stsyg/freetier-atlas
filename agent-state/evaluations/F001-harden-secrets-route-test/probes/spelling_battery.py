"""Spelling battery: is the route rule computed from STRUCTURE or fitted to the
shapes that happen to appear in ci.yml / .pre-commit-config.yaml today?

Every case here is a spelling that NONE of the four route files currently uses.
Two kinds:

  LEGIT  a schema-valid, genuinely-executed spelling. Must be ACCEPTED.
         A rejection is a false positive and means the rule was fitted to
         today's shapes rather than derived from the schema.

  DECOY  a schema-valid spelling that is NOT executed by the tool. Must be
         REJECTED. An acceptance means the rule cannot tell an executed
         position from a lexically similar inert one.

RULES is re-declared locally ON PURPOSE and must be re-read from the PR's
ROUTE_RULES table every time the head moves.
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

# !!! RE-READ FROM THE PR's ROUTE_RULES EVERY TIME THE HEAD MOVES !!!
CI_STEP_RUN = (("jobs", "*", "steps[]", "run"), True)
PRECOMMIT_ENTRY = (("repos[]", "hooks[]", "entry"), False)
PRECOMMIT_ARGS = (("repos[]", "hooks[]", "args[]"), False)

RULES = {
    CI: (None, (CI_STEP_RUN,)),
    PC: (None, (PRECOMMIT_ENTRY, PRECOMMIT_ARGS)),
}

INVOKE = f"python scripts/{VAL}.py"

CASES: list[tuple[str, str, str, bool]] = []


def add(kind: str, label: str, route: str, must_pass: bool, text: str) -> None:
    CASES.append((kind, label, route, must_pass, text))  # type: ignore[arg-type]


# ---------------------------------------------------------------- ci.yml LEGIT
add(
    "LEGIT",
    "step-level run: after a nested env: block",
    CI,
    True,
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Validate\n"
    "        env:\n"
    "          FOO: bar\n"
    f"        run: {INVOKE}\n",
)
add(
    "LEGIT",
    "dash and key on one line: '- run: |'",
    CI,
    True,
    f"jobs:\n  lint:\n    steps:\n      - run: |\n          {INVOKE}\n",
)
add(
    "LEGIT",
    "4-space indent style throughout",
    CI,
    True,
    "jobs:\n    lint:\n        steps:\n"
    "            - name: Validate\n"
    "              run: |\n"
    f"                  {INVOKE}\n",
)
add(
    "LEGIT",
    "run: inside a matrix job",
    CI,
    True,
    "jobs:\n  lint:\n    strategy:\n      matrix:\n        os: [ubuntu-latest]\n"
    "    runs-on: ${{ matrix.os }}\n    steps:\n"
    "      - name: Validate\n"
    f"        run: {INVOKE}\n",
)
add(
    "LEGIT",
    "run: preceded by a with: on a PRIOR step",
    CI,
    True,
    "jobs:\n  lint:\n    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "        with:\n"
    "          fetch-depth: 0\n"
    "      - name: Validate\n"
    f"        run: {INVOKE}\n",
)
add(
    "LEGIT",
    "keep-chomped block scalar 'run: |-'",
    CI,
    True,
    f"jobs:\n  lint:\n    steps:\n      - run: |-\n          {INVOKE}\n",
)
add(
    "LEGIT",
    "explicit indent indicator 'run: |2'",
    CI,
    True,
    f"jobs:\n  lint:\n    steps:\n      - run: |2\n          {INVOKE}\n",
)
add(
    "LEGIT",
    "double-quoted inline run value",
    CI,
    True,
    f'jobs:\n  lint:\n    steps:\n      - run: "{INVOKE}"\n',
)
add(
    "LEGIT",
    "flow mapping step  - {name: s, run: ...}",
    CI,
    True,
    f"jobs:\n  lint:\n    steps:\n      - {{name: Validate, run: {INVOKE}}}\n",
)
add(
    "LEGIT",
    "flow mapping hook  - {id: s, entry: ...}",
    PC,
    True,
    f"repos:\n  - repo: local\n    hooks:\n      - {{id: sb, entry: {INVOKE}, language: system}}\n",
)
add(
    "LEGIT",
    "anchor at inert key aliased into run:",
    CI,
    True,
    "x-templates:\n"
    f"  secrets: &secrets {INVOKE}\n"
    "jobs:\n  lint:\n    steps:\n"
    "      - name: Validate\n        run: *secrets\n",
)
add(
    "LEGIT",
    "CRLF line endings throughout",
    CI,
    True,
    (
        f"jobs:\n  lint:\n    steps:\n      - name: Validate\n        run: |\n          {INVOKE}\n"
    ).replace("\n", "\r\n"),
)

# ---------------------------------------------------------------- ci.yml DECOY
add(
    "DECOY",
    "with: run: |   (action INPUT, block)",
    CI,
    False,
    "jobs:\n  lint:\n    steps:\n"
    "      - uses: third/party@v1\n"
    "        with:\n"
    "          run: |\n"
    f"            {INVOKE}\n",
)
add(
    "DECOY",
    "with: run:     (action INPUT, inline)",
    CI,
    False,
    "jobs:\n  lint:\n    steps:\n"
    "      - uses: third/party@v1\n"
    "        with:\n"
    f"          run: {INVOKE}\n",
)
add(
    "DECOY",
    "env: run:      (env var named run)",
    CI,
    False,
    "jobs:\n  lint:\n    steps:\n"
    "      - name: x\n"
    "        env:\n"
    f"          run: {INVOKE}\n"
    "        run: echo hi\n",
)
add(
    "DECOY",
    "defaults.run.shell mapping",
    CI,
    False,
    f"defaults:\n  run:\n    shell: {INVOKE}\njobs:\n  lint:\n    steps:\n      - run: echo hi\n",
)
add(
    "DECOY",
    "on.workflow_call.inputs.run.default",
    CI,
    False,
    "on:\n  workflow_call:\n    inputs:\n      run:\n"
    f"        default: {INVOKE}\n"
    "jobs:\n  lint:\n    steps:\n      - run: echo hi\n",
)

# --------------------------------------------------------- pre-commit LEGIT
add(
    "LEGIT",
    "multi-item block-sequence args:",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n        entry: python\n        args:\n"
    "          - --strict\n"
    f"          - scripts/{VAL}.py\n"
    "        language: system\n",
)
add(
    "LEGIT",
    "entry: with keys after it",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    f"        entry: {INVOKE}\n"
    "        files: ^x$\n        language: system\n",
)
add(
    "LEGIT",
    "single-quoted entry value",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    f"        entry: '{INVOKE}'\n"
    "        language: system\n",
)
add(
    "LEGIT",
    "second hook after an unrelated first",
    PC,
    True,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: other\n        entry: echo hi\n        language: system\n"
    "      - id: sb\n"
    f"        entry: {INVOKE}\n"
    "        language: system\n",
)

# --------------------------------------------------------- pre-commit DECOY
add(
    "DECOY",
    "hook name: only",
    PC,
    False,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    f"        name: runs {VAL}.py\n"
    "        entry: python other.py\n        language: system\n",
)
add(
    "DECOY",
    "hook description: | prose block",
    PC,
    False,
    "repos:\n  - repo: local\n    hooks:\n"
    "      - id: sb\n"
    "        description: |\n"
    f"          historically ran {INVOKE}\n"
    "        entry: python other.py\n        language: system\n",
)
add(
    "DECOY",
    "name: | block scalar",
    CI,
    False,
    f"jobs:\n  lint:\n    steps:\n      - name: |\n          {INVOKE}\n        run: echo hi\n",
)


def main() -> int:
    width = max(len(c[1]) for c in CASES) + 2
    bad: list[str] = []
    for kind in ("LEGIT", "DECOY"):
        print(f"\n===== {kind} " + "=" * (58 - len(kind)))
        for k, label, route, must_pass, text in CASES:
            if k != kind:
                continue
            contexts, keys = RULES[route]
            problems = ss.invocation_problems(route, text, VAL, contexts, keys)
            passed = problems == []
            got = "ACCEPT" if passed else "REJECT"
            want = "ACCEPT" if must_pass else "REJECT"
            ok = passed == must_pass
            print(f"{'   ' if ok else '>>>'} {label:<{width}} want={want:<7} got={got}")
            if not ok:
                bad.append(
                    (
                        "FALSE POSITIVE (legit spelling rejected): "
                        if kind == "LEGIT"
                        else "BYPASS (inert spelling accepted): "
                    )
                    + label
                )
    print("\n===================== BATTERY FINDINGS =====================")
    print("none" if not bad else "\n".join(" * " + b for b in bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
