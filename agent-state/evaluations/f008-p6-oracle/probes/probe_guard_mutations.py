"""Prove the corrected head's THREE prose guards are non-vacuous, by mutation.

A guard that cannot fail proves nothing, and a prose guard is the easiest kind to
write vacuously. Each guard below is attacked with the specific edit it exists to
catch. Every file is restored and the restoration is proven with ``git
hash-object`` against the pre-mutation blob -- never by ``git diff``, which
reports difference rather than absence and would be silent if a path were wrong.

The orchestrator asked two specific questions, and each gets its own mutation:

* does the retraction guard forbid ASSERTION rather than OCCURRENCE?  (M1, M2)
* can it be evaded by REWORDING the claim instead of dropping it?     (M3, M4)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
PY = REPO / ".venv" / "Scripts" / "python.exe"

ORACLE = REPO / "apps" / "api" / "app" / "ingest" / "adapters" / "profiles" / "oracle.py"
DOC = REPO / "docs" / "PROVIDER_ADAPTERS.md"
YAML = REPO / "config" / "examples" / "providers" / "oracle.example.yaml"

GUARDS = (
    "test_no_shipped_prose_ASSERTS_the_refuted_universal",
    "test_the_corrected_prose_states_its_scope_and_does_not_misfile_cloudflare",
    "test_oracle_is_the_only_provider_withheld_by_a_quoted_card_requirement",
    "test_perpetuity_neither_entails_nor_precludes_zero_cost",
    "test_the_capture_counts_only_blocks_that_actually_guard",
    "test_every_exported_constant_is_pinned_by_some_profile",
)
CONTAINERS = "test_the_containers_rationale_says_which_document_carries_which_entry"


def blob(path: Path) -> str:
    out = subprocess.run(
        ["git", "hash-object", str(path)], cwd=REPO, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def run(selector: str, target: str) -> bool:
    """True when GREEN."""

    proc = subprocess.run(
        [str(PY), "-m", "pytest", target, "-q", "-k", selector, "--no-header"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


UNIT = "tests/unit/test_adapter_oracle.py"
COVERAGE = "tests/unit/test_oracle_coverage_config.py"


def mutate(path: Path, old: str, new: str, label: str) -> tuple[str, bytes]:
    original = path.read_bytes()
    before = blob(path)
    text = original.decode("utf-8")
    assert text.count(old) >= 1, f"{label}: anchor not found, mutation would test nothing"
    patched = text.replace(old, new, 1)
    assert patched != text, f"{label}: mutation did not apply"
    path.write_bytes(patched.encode("utf-8"))
    print(f"  PATCHED {path.name}: {label}")
    print(f"    {old[:70]!r}\n    -> {new[:70]!r}")
    return before, original


def restore(path: Path, before: str, original: bytes, label: str) -> None:
    path.write_bytes(original)
    after = blob(path)
    status = "RESTORED byte-exact" if after == before else "RESTORE FAILED"
    print(f"    {status} ({label}): {before[:12]} -> {after[:12]}")
    assert after == before, f"{label}: restoration failed"


print("=" * 78)
print("BASELINE: every guard must be GREEN before anything is broken")
print("=" * 78)
for name in GUARDS:
    print(f"  {name:<70} {'GREEN' if run(name, UNIT) else 'RED'}")
print(f"  {CONTAINERS:<70} {'GREEN' if run(CONTAINERS, COVERAGE) else 'RED'}")

print("\n" + "=" * 78)
print("M1 - ASSERT the refuted universal with NO refuting context (must go RED)")
print("=" * 78)
before, original = mutate(
    ORACLE,
    'here is not inferred from the word "free".',
    'here is not inferred from the word "free". Perpetual is not free.',
    "M1 bare assertion",
)
print(f"    guard -> {'GREEN (VACUOUS!)' if run(GUARDS[0], UNIT) else 'RED (correct)'}")
restore(ORACLE, before, original, "M1")

print("\n" + "=" * 78)
print("M2 - QUOTE the phrase inside refuting context (must STAY GREEN)")
print("=" * 78)
before, original = mutate(
    ORACLE,
    'here is not inferred from the word "free".',
    'here is not inferred from the word "free". An earlier revision said '
    '"perpetual is not free", which was measured FALSE and is retracted.',
    "M2 quoted-in-retraction",
)
print(f"    guard -> {'GREEN (correct)' if run(GUARDS[0], UNIT) else 'RED (over-strict!)'}")
restore(ORACLE, before, original, "M2")

print("\n" + "=" * 78)
print("M3 - REWORDING EVASION: 'not' -> 'never' (must still go RED)")
print("=" * 78)
before, original = mutate(
    ORACLE,
    'here is not inferred from the word "free".',
    'here is not inferred from the word "free". Perpetual is never free.',
    "M3 reworded evasion",
)
print(f"    guard -> {'GREEN (EVADED!)' if run(GUARDS[0], UNIT) else 'RED (evasion blocked)'}")
restore(ORACLE, before, original, "M3")

print("\n" + "=" * 78)
print("M4 - REWORDING EVASION the guard CANNOT catch (documents its real limit)")
print("=" * 78)
before, original = mutate(
    ORACLE,
    'here is not inferred from the word "free".',
    'here is not inferred from the word "free". A perpetual tier is always billed '
    "eventually, on every provider measured.",
    "M4 novel phrasing",
)
green = run(GUARDS[0], UNIT)
print(f"    guard -> {'GREEN (not caught - phrase-list limit)' if green else 'RED'}")
restore(ORACLE, before, original, "M4")

print("\n" + "=" * 78)
print("M5 - SWAP THE SCOPED COUNTS 3 <-> 5 (must go RED)")
print("=" * 78)
before, original = mutate(
    ORACLE,
    "Restricted to the six F008 providers the Z0\ncount is 3",
    "Restricted to the six F008 providers the Z0\ncount is 5",
    "M5 scope count swap",
)
print(f"    guard -> {'GREEN (VACUOUS!)' if run(GUARDS[1], UNIT) else 'RED (correct)'}")
restore(ORACLE, before, original, "M5")

print("\n" + "=" * 78)
print("M6 - REINSTATE the misleading APEX 'BOTH documents' claim (must go RED)")
print("=" * 78)
before, original = mutate(
    YAML,
    "NOT as an Always Free service entry",
    "an entry appearing on BOTH documents",
    "M6 APEX regression",
)
print(f"    guard -> {'GREEN (VACUOUS!)' if run(CONTAINERS, COVERAGE) else 'RED (correct)'}")
restore(YAML, before, original, "M6")

print("\n" + "=" * 78)
print("FINAL: all guards GREEN again on the restored tree")
print("=" * 78)
for name in GUARDS:
    print(f"  {name:<70} {'GREEN' if run(name, UNIT) else 'RED'}")
print(f"  {CONTAINERS:<70} {'GREEN' if run(CONTAINERS, COVERAGE) else 'RED'}")
