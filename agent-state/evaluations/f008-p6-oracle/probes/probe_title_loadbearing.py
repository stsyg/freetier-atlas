"""Is every block the capture calls 'pinned' actually load-bearing?

Each Oracle capture states that "all N pinned block(s) occur EXACTLY ONCE in the
live document". On five of the six sources N is one greater than the number of
distinct blocks the profile asserts, and the surplus is the document ``<title>``:
the module defines ``*_TITLE`` constants for all six sources but only
``oracle_always_free_resources`` pins one as an assertion.

A pinned block must REJECT the document when it changes. This probe mutates the
``<title>`` of every source and reports whether extraction still succeeds. Where
it succeeds, that block is not a guard, and counting it as "pinned" overstates
the reconciliation.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.ingest import resolve_profile  # noqa: E402

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


def extract_error(case: str, body: bytes) -> str | None:
    fixture = load_case("oracle", "html", case)
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=body)
    candidates = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    if not candidates:
        return "no_candidate"
    return candidates[0].facts.get("error")  # type: ignore[return-value]


print(f"{'case':<38} {'title_pinned':>12} {'baseline':>10} {'title_mutated':>15}")
print("-" * 80)

not_load_bearing = []
for case in CASES:
    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    title_pins = [a for a in profile.assertions if a.scope == "title"]
    source = fixture.content.decode("utf-8")

    baseline = extract_error(case, source.encode())

    start = source.index("<title>") + len("<title>")
    end = source.index("</title>")
    mutated = source[:start] + "Totally Different Oracle Page Title" + source[end:]
    after = extract_error(case, mutated.encode())

    print(f"{case:<38} {str(bool(title_pins)):>12} {str(baseline):>10} {str(after):>15}")
    if not title_pins and after is None:
        not_load_bearing.append(case)

print()
print("Sources where the <title> is counted by the capture but is NOT load-bearing:")
for case in not_load_bearing:
    print(f"  {case}")
print(f"\nCOUNT = {len(not_load_bearing)}")
