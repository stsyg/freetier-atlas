"""Mutation-test the PR's own control tests: would each fail if its guarantee went?

A control that cannot fail is decoration. This removes each guarantee in turn,
runs only the test that claims to guard it, and requires RED. Then it restores
the file and proves restoration by blob hash, never by numstat.

Usage:  python mutate_controls.py <worktree> <venv-python>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WT = Path(sys.argv[1])
PY = sys.argv[2]
TARGET = WT / "tests" / "support" / "source_scan.py"
REL = "tests/support/source_scan.py"

MUTATIONS = [
    (
        "F3 class-fix: diagnostics failure must not swallow the rejection",
        "tests/unit/test_secrets_baseline.py::test_a_failure_to_explain_never_becomes_a_failure_to_reject",
        "    except Exception as error:  # noqa: BLE001 - deliberate, see below",
        "    except KeyboardInterrupt as error:  # MUTATED: no longer catches MemoryError",
    ),
    (
        "F4 loader boundary: verdict must follow safe_load, not compose",
        "tests/unit/test_secrets_baseline.py::test_safe_load_is_authoritative_over_compose",
        "    for value in values:\n        if not value.shell:",
        "    if survives_in_the_composed_document(text, needle):\n        return []\n"
        "    for value in values:\n        if not value.shell:",
    ),
    (
        "F2 multi-document must be named",
        "tests/unit/test_secrets_baseline.py::test_a_multi_document_file_says_so",
        'multi = "expected a single document" in str(error)',
        "multi = False",
    ),
]


def blob(path: str) -> str:
    return subprocess.run(
        ["git", "hash-object", path], cwd=WT, capture_output=True, text=True, check=True
    ).stdout.strip()


def committed_blob(path: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", f"HEAD:{path}"], cwd=WT, capture_output=True, text=True, check=True
    ).stdout.strip()


def run(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", node, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=WT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def main() -> int:
    original = TARGET.read_text(encoding="utf-8", newline="")
    pristine = committed_blob(REL)
    print(f"committed blob {pristine}\nworking  blob {blob(REL)}\n")

    failures = []
    for label, node, old, new in MUTATIONS:
        print("=" * 74)
        print(label)
        # Baseline: the test must PASS before we break anything.
        code, _ = run(node)
        print(f"  baseline (unmutated) : {'PASS' if code == 0 else 'FAIL'}")
        if code != 0:
            failures.append(f"{label}: baseline already failing, mutation meaningless")
            continue

        if old not in original:
            failures.append(f"{label}: ANCHOR NOT FOUND — mutation never applied")
            print("  >>> anchor not found; skipping (this is an instrument failure)")
            continue

        TARGET.write_text(original.replace(old, new, 1), encoding="utf-8", newline="")
        assert blob(REL) != pristine, "mutation did not change the file"
        code, out = run(node)
        verdict = "RED (good)" if code != 0 else "GREEN  <<< VACUOUS CONTROL"
        print(f"  with guarantee removed: {verdict}")
        if code == 0:
            failures.append(f"{label}: control stayed GREEN with its guarantee removed")
        else:
            tail = [ln for ln in out.splitlines() if "assert" in ln or "Error" in ln]
            if tail:
                print(f"    {tail[0][:120]}")

        TARGET.write_text(original, encoding="utf-8", newline="")
        assert blob(REL) == pristine, "RESTORE FAILED"
        print("  restored, blob matches committed")

    print("\n" + "=" * 74)
    print(f"final blob {blob(REL)}  == committed {pristine}: {blob(REL) == pristine}")
    print("\n===================== CONTROL FINDINGS =====================")
    print(
        "none — every control failed when its guarantee was removed"
        if not failures
        else "\n".join(" * " + f for f in failures)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
