"""Re-verify the CORRECTED capture counts at head c4a7180f.

The correction replaced a prose-only count with two machine-readable fields,
``pinned_block_count`` and ``retained_not_pinned_count``. Both are re-derived
here from the registered profile objects and from the repository's own parse of
the committed capture -- never from the capture's own prose.

A pinned block must GUARD the document: mutating it must reject. Every block the
capture now calls pinned is mutated and checked, and every block it calls
retained-but-not-pinned is checked to confirm it does NOT guard. Getting that
second half wrong in the other direction (calling a real guard "furniture") would
understate the fixture just as badly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.ingest import resolve_profile  # noqa: E402
from app.ingest.adapters.html import _DocumentCollector  # noqa: E402

from tests.support.fixtures import build_fixture_adapter, load_case  # noqa: E402

DOMAINS = ("oracle.com", "www.oracle.com", "docs.oracle.com")
CASES = (
    "oracle-always-free-resources",
    "oracle-free-tier",
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-free-credit-promotion",
    "oracle-mysql-heatwave-always-free",
)

#: The load-bearing counts the evaluator derived independently at the old head.
EVALUATOR_DERIVED = {
    "oracle-always-free-resources": 24,
    "oracle-free-tier": 6,
    "oracle-always-free-services": 7,
    "oracle-cloud-free-tier": 6,
    "oracle-free-credit-promotion": 5,
    "oracle-mysql-heatwave-always-free": 7,
}


def extract_error(case: str, body: bytes) -> str | None:
    fixture = load_case("oracle", "html", case)
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=body)
    cands = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    if not cands:
        return "no_candidate"
    return cands[0].facts.get("error")  # type: ignore[return-value]


print(f"{'case':<38}{'field':>7}{'derived':>9}{'evalr':>7}  agree")
print("-" * 74)

mismatches = []
for case in CASES:
    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))

    declared = capture.get("pinned_block_count")
    derived = len({a.text for a in profile.assertions})
    expected = EVALUATOR_DERIVED[case]
    agree = declared == derived == expected
    if not agree:
        mismatches.append((case, declared, derived, expected))
    print(f"{case:<38}{str(declared):>7}{derived:>9}{expected:>7}  {'OK' if agree else 'MISMATCH'}")

print()
if mismatches:
    for case, declared, derived, expected in mismatches:
        print(f"  MISMATCH {case}: capture={declared} derived={derived} evaluator={expected}")
else:
    print("All corrected pinned_block_count values agree with the independently derived counts.")

print("\n--- Are ALL declared-pinned blocks actually load-bearing? (delete -> must reject) ---")
not_guarding = []
for case in CASES:
    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    source = fixture.content.decode("utf-8")
    checked = 0
    for text in sorted({a.text for a in profile.assertions}):
        norm = " ".join(text.split())
        collector = _DocumentCollector()
        collector.feed(source)
        collector.close()
        target = None
        for block in collector.text_blocks:
            if " ".join(block.text.split()) == norm:
                target = block.text
                break
        if target is None:
            continue
        # Reword the block by appending a sentence; whole-block equality must fail.
        mutated = source.replace(target, target + " EVALUATOR MUTATION SENTENCE.", 1)
        if mutated == source:
            continue
        checked += 1
        if extract_error(case, mutated.encode()) is None:
            not_guarding.append((case, norm[:70]))
    print(f"{case:<38} mutated {checked} pinned block(s)")

print()
if not_guarding:
    print("BLOCKS DECLARED PINNED THAT DO NOT GUARD:")
    for case, text in not_guarding:
        print(f"  {case}: {text}")
else:
    print("Every declared-pinned block REJECTS the document when reworded. All are guards.")

print("\n--- Is the <title> correctly excluded where it is not pinned? ---")
for case in CASES:
    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    title_pinned = bool([a for a in profile.assertions if a.scope == "title"])
    source = fixture.content.decode("utf-8")
    start = source.index("<title>") + len("<title>")
    end = source.index("</title>")
    mutated = source[:start] + "Evaluator Replaced Title" + source[end:]
    err = extract_error(case, mutated.encode())
    guards = err is not None
    consistent = title_pinned == guards
    print(
        f"{case:<38} pinned={str(title_pinned):<6} guards={str(guards):<6} "
        f"retained_not_pinned={capture.get('retained_not_pinned_count')}  "
        f"{'OK' if consistent else 'INCONSISTENT'}"
    )
