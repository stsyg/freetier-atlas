"""Counterfactual: what happens if the evaluator REJECTS the card reading?

The builder reads two Oracle blocks as ``requires_card = True``. Neither contains
the word "required" and one hedges with "most users", so the reading is a
judgement. The builder asserts that rejecting it changes no Z0 verdict -- the
affected offers would move from ``Z1_BILLING_EXPOSURE`` to ``UNKNOWN`` via gate 4
instead of gate 3, and nothing would become Z0.

That is the claim this probe measures, by re-classifying every Oracle offer with
the card fact forcibly removed, and then with it forcibly set to False (the
strongest possible reading in the builder's disfavour).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.classify.engine import OfferFacts, classify  # noqa: E402

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

#: The identity-verification block, shared verbatim by three documents.
IDENTITY_BLOCK_CASES = (
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-mysql-heatwave-always-free",
)
#: The "most users need ... a credit card" block, unique to the OCI Free Tier doc.
MOST_USERS_BLOCK_CASES = ("oracle-free-tier",)


def facts_for(case: str) -> dict[str, object]:
    fixture = load_case("oracle", "html", case)
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS)
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    return dict(candidate.facts)


def verdict(facts: dict[str, object], card_override: object = "keep") -> str:
    behaviours: tuple[str, ...] = ()
    if facts.get("exhaustion_behaviour"):
        behaviours = (str(facts["exhaustion_behaviour"]),)
    card = facts.get("requires_card") if card_override == "keep" else card_override
    return classify(
        OfferFacts(
            offer_type=str(facts.get("offer_type")),
            requires_card=card,  # type: ignore[arg-type]
            has_paid_dependencies=facts.get("has_paid_dependencies"),  # type: ignore[arg-type]
            exhaustion_behaviours=behaviours,
        )
    ).zero_cost_class


rows = []
for case in CASES:
    f = facts_for(case)
    rows.append(
        (
            case,
            verdict(f),
            verdict(f, None),
            verdict(f, False),
        )
    )

print(f"{'case':<38} {'as-shipped':>12} {'card=absent':>12} {'card=False':>12}")
print("-" * 78)
for case, shipped, absent, false_ in rows:
    print(f"{case:<38} {shipped:>12} {absent:>12} {false_:>12}")

print()
print("SCENARIO 1 - reject ONLY the identity-verification block (affects 3 offers):")
moved = []
for case, shipped, absent, _ in rows:
    if case in IDENTITY_BLOCK_CASES and shipped != absent:
        moved.append((case, shipped, absent))
for case, shipped, absent in moved:
    print(f"   {case}: {shipped} -> {absent}")
print(f"   offers moved = {len(moved)}")

print()
print("SCENARIO 2 - reject ONLY the 'most users' block (affects 1 offer):")
for case, shipped, absent, _ in rows:
    if case in MOST_USERS_BLOCK_CASES and shipped != absent:
        print(f"   {case}: {shipped} -> {absent}")

print()
print("SCENARIO 3 - reject BOTH readings (card absent everywhere):")
absent_all = [absent for _, _, absent, _ in rows]
print(f"   verdicts = {absent_all}")
print(f"   ANY Z0 = {'Z0_TRUE_FREE' in absent_all}")

print()
print("SCENARIO 4 - ADVERSARIAL: force card=False everywhere (strongest anti-builder reading):")
false_all = [f for _, _, _, f in rows]
print(f"   verdicts = {false_all}")
print(f"   ANY Z0 = {'Z0_TRUE_FREE' in false_all}")
print(
    "\n   NOTE: even asserting the OPPOSITE of the builder's reading produces no Z0, "
    "because has_paid_dependencies is unknown on every Oracle source (gate 4)."
)
