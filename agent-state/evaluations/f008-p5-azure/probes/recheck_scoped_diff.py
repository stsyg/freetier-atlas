"""Scoped re-check instrument 1: prove the correction is text-only.

Compares the evaluated head against a corrected head and classifies every changed
path against the allowlist of locations the correction is permitted to touch. Any
change outside that allowlist means the rest of the Level-2 evaluation no longer
composes and must be re-derived rather than reused.

Usage:  python recheck_scoped_diff.py <evaluated_sha> <corrected_sha>
"""

from __future__ import annotations

import subprocess
import sys

# The six committed locations carrying the "one unknown" claim, plus the test
# module that is renamed and strengthened. Nothing else may move.
ALLOWED = {
    "apps/api/app/ingest/adapters/profiles/azure.py",
    "config/examples/providers/azure.example.yaml",
    "docs/PROVIDER_ADAPTERS.md",
    "agent-state/current_contract.json",
    "agent-state/progress.md",
    "tests/unit/test_adapter_azure.py",
}

# Blobs that MUST be byte-identical for the earlier evaluation to compose.
MUST_NOT_MOVE = (
    "agent-state/feature_list.json",
    "apps/api/app/classify/engine.py",
    "apps/api/app/ingest/adapters/html.py",
    "apps/api/app/ingest/fetch.py",
    "apps/api/app/models/vocab.py",
    "tests/support/fixtures.py",
    "tests/support/source_scan.py",
    "scripts/capture_fixture.py",
    ".github/workflows/ci.yml",
)


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def blob(sha: str, path: str) -> str | None:
    result = subprocess.run(["git", "rev-parse", f"{sha}:{path}"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    old, new = sys.argv[1], sys.argv[2]

    changed = [line.split("\t") for line in git("diff", "--numstat", old, new).splitlines() if line]
    print("=" * 78)
    print(f"CHANGED PATHS  {old[:7]} -> {new[:7]}")
    print("=" * 78)
    unexpected = []
    for added, removed, path in changed:
        verdict = "allowed" if path in ALLOWED else "UNEXPECTED"
        if path not in ALLOWED:
            unexpected.append(path)
        print(f"  +{added:>5} -{removed:>5}  {path:<58} {verdict}")

    print()
    print(f"changed paths: {len(changed)}   outside allowlist: {len(unexpected)}")
    if unexpected:
        print("  -> the correction is NOT text-only; re-derive rather than compose:")
        for path in unexpected:
            print(f"     {path}")

    print()
    print("=" * 78)
    print("BLOBS THAT MUST NOT MOVE (composition guard)")
    print("=" * 78)
    moved = []
    for path in MUST_NOT_MOVE:
        before, after = blob(old, path), blob(new, path)
        if before is None or after is None:
            print(f"  {path:<52} PATH-NOT-FOUND (before={before}, after={after})")
            moved.append(path)
            continue
        same = before == after
        if not same:
            moved.append(path)
        print(f"  {path:<52} {'IDENTICAL' if same else 'MOVED'}  {before[:12]}")

    print()
    print(f"RESULT: text-only correction = {not unexpected and not moved}")
    if unexpected or moved:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
