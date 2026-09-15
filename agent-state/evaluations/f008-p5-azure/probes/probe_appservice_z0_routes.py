"""Level-2 evaluator probe: re-derive the Azure App Service near-miss.

Answers three questions the builder's report asserts but does not measure in the
open:

1. What are App Service's extracted facts, verbatim from the committed fixture?
2. How many blocking conditions does the REAL classifier return, and is the card
   the ONLY one? ("fails on the card gate alone" / "one unknown from Z0".)
3. Enumerate EVERY route from the measured facts to Z0_TRUE_FREE by exhaustive
   search over the tri-state fields, and report how many exist.

Run with the PR checkout's repo root as argv[1].
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.classify.engine import OfferFacts, classify  # noqa: E402
from app.models.vocab import EXHAUSTION_BEHAVIOURS, OFFER_TYPES  # noqa: E402

from tests.support.fixtures import run_extraction_case  # noqa: E402

DOMAINS = ("azure.microsoft.com", "learn.microsoft.com")


def classify_facts(facts):
    behaviours = ()
    if facts.get("exhaustion_behaviour"):
        behaviours = (str(facts["exhaustion_behaviour"]),)
    return classify(
        OfferFacts(
            offer_type=str(facts["offer_type"]),
            requires_card=facts.get("requires_card"),
            has_paid_dependencies=facts.get("has_paid_dependencies"),
            exhaustion_behaviours=behaviours,
        )
    )


def main() -> None:
    _, (candidate,) = run_extraction_case(
        "azure", "html", "azure-app-service-quotas", official_domains=DOMAINS
    )
    facts = candidate.facts

    print("=" * 78)
    print("MEASURED App Service facts relevant to classification")
    print("=" * 78)
    for key in ("offer_type", "requires_card", "has_paid_dependencies", "exhaustion_behaviour"):
        present = key in facts
        print(f"  {key:26} present={present!s:5} value={facts.get(key)!r}")

    result = classify_facts(facts)
    print()
    print(f"VERDICT: {result.zero_cost_class}")
    print(f"BLOCKING CONDITION COUNT: {len(result.blocking_conditions)}")
    for i, cond in enumerate(result.blocking_conditions, 1):
        print(f"  [{i}] {cond}")

    card_only = (
        len(result.blocking_conditions) == 1 and "payment card" in (result.blocking_conditions[0])
    )
    print()
    print(f'CLAIM "fails on the card gate ALONE"  -> {"HOLDS" if card_only else "DOES NOT HOLD"}')
    print(
        f'CLAIM "ONE unknown separates it from Z0" -> '
        f"{'HOLDS' if len(result.blocking_conditions) == 1 else 'DOES NOT HOLD'}"
    )

    # ---------------------------------------------------------------- routes --
    # Exhaustive search: hold the two facts the DOCUMENT establishes (offer_type,
    # exhaustion_behaviour) and vary only the two tri-state fields the document
    # says nothing about. Then widen to every offer_type/behaviour combination to
    # count total routes to Z0 from this document's shape.
    print()
    print("=" * 78)
    print("ROUTE ENUMERATION: what would flip App Service to Z0?")
    print("=" * 78)

    tri = (True, False, None)
    print("\nA) Holding the document's OWN offer_type and exhaustion_behaviour fixed:")
    z0_routes = []
    for card, deps in itertools.product(tri, tri):
        r = classify(
            OfferFacts(
                offer_type=str(facts["offer_type"]),
                requires_card=card,
                has_paid_dependencies=deps,
                exhaustion_behaviours=(str(facts["exhaustion_behaviour"]),),
            )
        )
        flag = "  <== Z0" if r.zero_cost_class == "Z0_TRUE_FREE" else ""
        if r.zero_cost_class == "Z0_TRUE_FREE":
            z0_routes.append((card, deps))
        print(
            f"   requires_card={card!s:5} has_paid_dependencies={deps!s:5} -> "
            f"{r.zero_cost_class}{flag}"
        )
    print(f"\n   Z0 routes with the document's own facts held: {len(z0_routes)}")
    print(f"   -> requires BOTH unknowns resolved to False: {z0_routes}")

    print("\nB) How many of the 3x3 combinations are non-Z0 (i.e. safe)?")
    print(f"   {9 - len(z0_routes)} of 9")

    print("\nC) Widening over the whole closed vocabulary (offer_type x behaviour x 3 x 3):")
    total = 0
    z0_total = 0
    z0_by_type = {}
    for otype, beh, card, deps in itertools.product(
        sorted(OFFER_TYPES), sorted(EXHAUSTION_BEHAVIOURS), tri, tri
    ):
        total += 1
        r = classify(
            OfferFacts(
                offer_type=otype,
                requires_card=card,
                has_paid_dependencies=deps,
                exhaustion_behaviours=(beh,),
            )
        )
        if r.zero_cost_class == "Z0_TRUE_FREE":
            z0_total += 1
            z0_by_type.setdefault(otype, set()).add(beh)
    print(f"   combinations examined: {total}")
    print(f"   combinations yielding Z0: {z0_total}")
    print("   offer_types that can EVER reach Z0:")
    for otype in sorted(z0_by_type):
        print(f"     - {otype:26} via behaviours {sorted(z0_by_type[otype])}")
    print()
    print(
        f"   Is App Service's offer_type ({facts['offer_type']!r}) Z0-capable? "
        f"{facts['offer_type'] in z0_by_type}"
    )

    print()
    print("=" * 78)
    print("SUMMARY JSON")
    print("=" * 78)
    print(
        json.dumps(
            {
                "verdict": result.zero_cost_class,
                "blocking_condition_count": len(result.blocking_conditions),
                "blocking_conditions": list(result.blocking_conditions),
                "card_gate_is_sole_blocker": card_only,
                "z0_routes_holding_document_facts": len(z0_routes),
                "offer_type": facts["offer_type"],
                "offer_type_is_z0_capable": facts["offer_type"] in z0_by_type,
                "exhaustion_behaviour": facts.get("exhaustion_behaviour"),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
