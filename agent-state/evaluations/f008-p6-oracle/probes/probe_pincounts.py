"""Independent re-derivation of the per-source pinned-block and table-row counts.

The capture files state these counts in English prose. Prose can drift from the
profile it describes, so this probe recomputes both numbers from the registered
profile objects and from the committed source HTML, and compares.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.ingest import resolve_profile  # noqa: E402
from app.ingest.adapters.html import _DocumentCollector  # noqa: E402

from tests.support.fixtures import load_case  # noqa: E402

CASES = (
    "oracle-always-free-resources",
    "oracle-free-tier",
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-free-credit-promotion",
    "oracle-mysql-heatwave-always-free",
)

CLAIMED = {
    "oracle-always-free-resources": (24, 6),
    "oracle-free-tier": (7, 0),
    "oracle-always-free-services": (8, 0),
    "oracle-cloud-free-tier": (7, 0),
    "oracle-free-credit-promotion": (6, 0),
    "oracle-mysql-heatwave-always-free": (8, 0),
}

print(f"{'case':<38} {'claimed':>8} {'distinct':>9} {'assertions':>11} {'rows':>5} {'live':>5}")
print("-" * 82)

failures = []
for case in CASES:
    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    distinct = len({a.text for a in profile.assertions})
    total_assertions = len(profile.assertions)
    rows = len(profile.matrix_rows or {})

    source = fixture.content.decode("utf-8")
    collector = _DocumentCollector()
    collector.feed(source)
    collector.close()
    # Live body rows in the ONE target table, counted from the committed markup.
    body = source.split("<tbody>", 1)[1] if "<tbody>" in source else ""
    live_rows = len(re.findall(r"<tr[^>]*>", body))

    claimed_pins, claimed_rows = CLAIMED[case]
    mark = "OK " if distinct == claimed_pins else "MISMATCH"
    if distinct != claimed_pins:
        failures.append((case, claimed_pins, distinct))
    print(
        f"{case:<38} {claimed_pins:>8} {distinct:>9} {total_assertions:>11} "
        f"{rows:>5} {live_rows:>5}  {mark}"
    )

print()
if failures:
    print("PIN-COUNT DISAGREEMENTS (capture prose vs recomputed distinct assertion texts):")
    for case, claimed, actual in failures:
        print(f"  {case}: capture says {claimed}, profile has {actual} distinct pinned blocks")
else:
    print("All pin counts agree with the capture prose.")

print("\n--- title-scoped assertions per profile (explains any +/-1) ---")
for case in CASES:
    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    titles = [a.text for a in profile.assertions if a.scope == "title"]
    doc_blocks = {a.text for a in profile.assertions if a.scope != "title"}
    print(f"{case:<38} title_pins={len(titles)} doc_block_pins={len(doc_blocks)}")

print("\n--- capture prose, verbatim numbers extracted ---")
for case in CASES:
    cap = json.loads(
        (load_case("oracle", "html", case).directory / "capture.json").read_text(encoding="utf-8")
    )
    text = cap["live_reconciliation"]
    pins = re.search(r"all (\d+) pinned block", text)
    rws = re.search(r"all (\d+) live target-table row", text)
    pin_n = pins.group(1) if pins else "?"
    row_n = rws.group(1) if rws else "?"
    print(f"{case:<38} prose_pins={pin_n} prose_rows={row_n}")
