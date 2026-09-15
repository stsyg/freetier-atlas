"""Independent probe: load source_scan.py by path, exercise ROUTE_RULES semantics.

Deliberately does NOT import the test module, so the instrument does not share
the code under test beyond source_scan itself (which is the thing being probed).
ROUTE_RULES is re-declared here from the PR's table.
"""

import importlib.util
import sys

SCAN_PATH = sys.argv[1]

spec = importlib.util.spec_from_file_location("source_scan", SCAN_PATH)
source_scan = importlib.util.module_from_spec(spec)
sys.modules["source_scan"] = source_scan
spec.loader.exec_module(source_scan)

VAL = "check_secrets_baseline"

CI = ".github/workflows/ci.yml"
PC = ".pre-commit-config.yaml"
SH = "scripts/check.sh"
PS = "scripts/check.ps1"

RULES = {
    CI: (frozenset({"shell"}), None),
    PC: (frozenset({"yaml"}), frozenset({"entry", "args"})),
    PS: (frozenset({"powershell"}), None),
    SH: (frozenset({"shell"}), None),
}

CASES = [
    # (label, route, expect_pass, text)
    (
        "CONTROL ci.yml block run: |  (shape shipped today)",
        CI,
        True,
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - name: Validate secrets baseline\n"
        "        run: |\n"
        "          python scripts/check_secrets_baseline.py --require-reference\n",
    ),
    (
        "LEAD ci.yml flow run: (single-line, idiomatic)",
        CI,
        True,
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - name: Validate secrets baseline\n"
        "        run: python scripts/check_secrets_baseline.py --require-reference\n",
    ),
    (
        "LEAD ci.yml folded run: > ",
        CI,
        True,
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - name: Validate secrets baseline\n"
        "        run: >\n"
        "          python scripts/check_secrets_baseline.py --require-reference\n",
    ),
    (
        "CONTROL pre-commit flow entry: (shape shipped today)",
        PC,
        True,
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: secrets-baseline-shape\n"
        "        name: Validate .secrets.baseline shape\n"
        "        entry: python scripts/check_secrets_baseline.py\n"
        "        language: system\n",
    ),
    (
        "KNOWN-F pre-commit block-sequence args:",
        PC,
        True,
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: secrets-baseline-shape\n"
        "        entry: python\n"
        "        args:\n"
        "          - scripts/check_secrets_baseline.py\n"
        "        language: system\n",
    ),
    (
        "LEAD pre-commit block-scalar entry: |",
        PC,
        True,
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: secrets-baseline-shape\n"
        "        entry: |\n"
        "          python scripts/check_secrets_baseline.py\n"
        "        language: system\n",
    ),
    (
        "LEAD pre-commit flow-seq args: [ ... ]",
        PC,
        True,
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: secrets-baseline-shape\n"
        "        entry: python\n"
        "        args: [scripts/check_secrets_baseline.py]\n"
        "        language: system\n",
    ),
    (
        "NEGCTRL pre-commit needle only under name:  (MUST be rejected)",
        PC,
        False,
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: secrets-baseline-shape\n"
        "        name: runs check_secrets_baseline.py\n"
        "        entry: python scripts/something_else.py\n"
        "        language: system\n",
    ),
    (
        "NEGCTRL ci.yml needle only under name:  (MUST be rejected)",
        CI,
        False,
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - name: run check_secrets_baseline.py\n"
        "        run: |\n"
        "          echo hi\n",
    ),
]


def main() -> int:
    width = max(len(label) for label, *_ in CASES)
    bad = 0
    for label, route, expect_pass, text in CASES:
        contexts, keys = RULES[route]
        problems = source_scan.invocation_problems(route, text, VAL, contexts, keys)
        passed = problems == []
        verdict = "PASS" if passed else "REJECT"
        agree = "ok " if passed == expect_pass else "<<< MISMATCH"
        if passed != expect_pass:
            bad += 1
        print(
            f"{label:<{width}}  expect={'PASS' if expect_pass else 'REJECT':<6} "
            f"got={verdict:<6} {agree}"
        )
        if problems:
            print(f"{'':<{width}}    -> {problems[0][:190]}")
    print()
    print(f"mismatches: {bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
