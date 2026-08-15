"""Level-2 evaluator probe for the Oracle provider slice (F008 P6).

Re-derives the slice's material claims by a DIFFERENT method than the one that
produced them. The builder's own guards search a whitespace-normalised whole
document string; this probe works **block-wise over the repository's own parsed
blocks**, so a claim that is true line-wise but false block-wise (or vice versa)
shows up as a disagreement rather than as agreement between two searches that
share a blind spot.

Run with the repository root of the tree under test as ``argv[1]``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.classify.engine import OfferFacts, classify  # noqa: E402
from app.ingest import resolve_profile  # noqa: E402
from app.ingest.adapters.html import _DocumentCollector  # noqa: E402

from tests.support.fixtures import available_cases, build_fixture_adapter, load_case  # noqa: E402

ORACLE_SOURCES = (
    "oracle-always-free-resources",
    "oracle-free-tier",
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-free-credit-promotion",
    "oracle-mysql-heatwave-always-free",
)

#: Deliberately BROADER than the builder's two-phrase check ("credit card",
#: "credit/debit card"). If an absence-based refusal is honest, none of these
#: may appear in a payment-condition sense anywhere in the document's blocks.
PAYMENT_TERMS = (
    "credit card",
    "credit/debit card",
    "debit card",
    "payment card",
    "payment method",
    "payment verification",
    "card",
    "visa",
    "mastercard",
    "billing information",
    "purchase",
    "invoice",
)


def blocks_of(case: str, provider: str = "oracle") -> list[tuple[str, str]]:
    """Return (scope, text) for every block the repository's own parser sees."""

    fixture = load_case(provider, "html", case)
    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    return [(b.scope, b.text) for b in collector.text_blocks]


def extract(provider: str, case: str, domains: tuple[str, ...]) -> dict[str, object]:
    fixture = load_case(provider, "html", case)
    adapter = build_fixture_adapter(fixture, official_domains=domains)
    candidates = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    if len(candidates) != 1:
        return {"error": f"candidate_count={len(candidates)}"}
    return dict(candidates[0].facts)


def verdict_for(facts: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    behaviours: tuple[str, ...] = ()
    if facts.get("exhaustion_behaviour"):
        behaviours = (str(facts["exhaustion_behaviour"]),)
    result = classify(
        OfferFacts(
            offer_type=str(facts.get("offer_type")),
            requires_card=facts.get("requires_card"),  # type: ignore[arg-type]
            has_paid_dependencies=facts.get("has_paid_dependencies"),  # type: ignore[arg-type]
            exhaustion_behaviours=behaviours,
        )
    )
    return result.zero_cost_class, result.blocking_conditions


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    domains = ("oracle.com", "www.oracle.com", "docs.oracle.com")

    section("A. SIX ORACLE SOURCES: verdict re-derived block-wise from the fixtures")
    verdicts = []
    for case in ORACLE_SOURCES:
        facts = extract("oracle", case, domains)
        cls, blocking = verdict_for(facts)
        verdicts.append(cls)
        print(f"\n{case}")
        print(f"  offer_type            = {facts.get('offer_type')!r}")
        print(f"  requires_card         = {facts.get('requires_card')!r}")
        print(f"  has_paid_dependencies = {facts.get('has_paid_dependencies')!r}")
        print(f"  exhaustion_behaviour  = {facts.get('exhaustion_behaviour')!r}")
        print(f"  VERDICT               = {cls}")
        for b in blocking:
            print(f"     blocked-by: {b}")
    print(f"\nVERDICT SEQUENCE = {verdicts}")
    print(f"ANY Z0 AMONG ORACLE = {'Z0_TRUE_FREE' in verdicts}")

    section("B. ABSENCE CLAIM ATTACKED BLOCK-WISE (broader term list than the guard)")
    for case in ("oracle-always-free-resources", "oracle-free-credit-promotion"):
        print(f"\n{case}")
        hits = []
        for scope, text in blocks_of(case):
            low = text.lower()
            for term in PAYMENT_TERMS:
                if term in low:
                    hits.append((term, scope, text[:160]))
        if not hits:
            print("  NO payment-instrument term found in ANY parsed block. Absence confirmed.")
        for term, scope, text in hits:
            print(f"  HIT term={term!r} scope={scope}: {text}")

    section("C. CARD BLOCKS: does each pinned card block occur EXACTLY ONCE, block-wise?")
    for case in ORACLE_SOURCES:
        fixture = load_case("oracle", "html", case)
        profile = resolve_profile(fixture.profile)
        pins = [a for a in profile.assertions if a.field == "requires_card"]
        if not pins:
            print(f"\n{case}: no requires_card pin (absence-based)")
            continue
        norm = [" ".join(t.split()) for _, t in blocks_of(case)]
        for pin in pins:
            target = " ".join(pin.text.split())
            print(f"\n{case}: value={pin.value!r} occurrences={norm.count(target)}")

    section(
        "D. CROSS-PROVIDER: does ANY provider reach Z0? (tests the 'perpetual is not free' claim)"
    )
    provider_domains = {
        "aws": ("aws.amazon.com", "docs.aws.amazon.com"),
        "azure": ("azure.microsoft.com", "learn.microsoft.com", "www.microsoft.com"),
        "gcp": ("cloud.google.com", "firebase.google.com"),
        "github": ("docs.github.com", "github.com"),
        "vercel": ("vercel.com",),
        "cloudflare": ("developers.cloudflare.com", "www.cloudflare.com"),
        "oracle": domains,
    }
    z0_rows = []
    for provider, doms in provider_domains.items():
        for case in available_cases(provider, "html"):
            try:
                facts = extract(provider, case, doms)
            except Exception as exc:  # noqa: BLE001
                print(f"  {provider}/{case}: SKIP ({type(exc).__name__})")
                continue
            if facts.get("error"):
                continue
            if not facts.get("offer_type"):
                continue
            cls, _ = verdict_for(facts)
            if cls == "Z0_TRUE_FREE":
                z0_rows.append((provider, case, facts.get("offer_type")))
    print("\nZ0_TRUE_FREE offers found across ALL providers:")
    for provider, case, offer_type in z0_rows:
        print(f"  {provider:12s} {case:42s} offer_type={offer_type}")
    print(f"\nTOTAL Z0 OFFERS = {len(z0_rows)}")
    perpetual_z0 = [r for r in z0_rows if r[2] == "always_free"]
    print(f"PERPETUAL (always_free) OFFERS THAT ARE Z0 = {len(perpetual_z0)}")
    for provider, case, _ in perpetual_z0:
        print(f"  -> {provider}/{case}")

    section("E. POSITIVE CONTROL broken independently by the evaluator")
    import app.classify.engine as eng

    before = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    print(f"  baseline control verdict = {before.zero_cost_class} (must be Z0_TRUE_FREE)")
    saved = eng.SAFE_EXHAUSTION
    eng.SAFE_EXHAUSTION = frozenset()
    print(f"  PATCHED: app.classify.engine.SAFE_EXHAUSTION = frozenset() (was {len(saved)})")
    after = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    print(f"  crippled control verdict = {after.zero_cost_class} (control must go RED)")
    sweep_still_green = True
    for case in ORACLE_SOURCES:
        facts = extract("oracle", case, domains)
        cls, _ = verdict_for(facts)
        if cls == "Z0_TRUE_FREE":
            sweep_still_green = False
    print(f"  six-offer no-Z0 sweep still GREEN under the patch = {sweep_still_green}")
    eng.SAFE_EXHAUSTION = saved
    print(f"  restored SAFE_EXHAUSTION size = {len(eng.SAFE_EXHAUSTION)}")

    section("F. COVERAGE COUNTS re-derived from the shipped YAML")
    import yaml

    cfg = yaml.safe_load(
        (REPO / "config" / "examples" / "providers" / "oracle.example.yaml").read_text(
            encoding="utf-8"
        )
    )
    coverage = cfg.get("coverage", {})
    counts: dict[str, int] = {}
    for _cat, entry in coverage.items():
        state = entry.get("state") if isinstance(entry, dict) else str(entry)
        counts[state] = counts.get(state, 0) + 1
    print(f"  coverage states = {json.dumps(counts, indent=2, sort_keys=True)}")
    print(f"  total categories = {sum(counts.values())}")
    print(f"  sources declared = {len(cfg.get('sources', []))}")


if __name__ == "__main__":
    main()
