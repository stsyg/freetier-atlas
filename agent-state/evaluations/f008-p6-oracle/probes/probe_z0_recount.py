"""Re-derive the Z0 count from scratch, at every granularity a corrected
sentence might use.

The evaluator's first pass counted Z0 by iterating fixture cases and skipping any
source that did not yield exactly one candidate. That is unsafe twice over: a
matrix source can legitimately yield several candidates (one per plan row), and
document-case fixtures ("unchanged", "changed", ...) are synthetic scaffolds
bound to a real profile, so counting them inflates the total.

This probe fixes both. It extracts EVERY candidate, classifies each with the real
engine, and reports the count separately by candidate, by declared source, by
service and by provider -- and separately for the six F008 providers versus the
whole repository, because Cloudflare is an F005 provider relocated behind the
F008 seam and is not one of the six.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402
from app.classify.engine import OfferFacts, classify  # noqa: E402

from tests.support.fixtures import available_cases, build_fixture_adapter, load_case  # noqa: E402

#: Provider -> (F008 slice label or None, official domains for the fetch policy).
PROVIDERS = {
    "github": ("P1", ("docs.github.com", "github.com")),
    "vercel": ("P2", ("vercel.com",)),
    "gcp": ("P3", ("cloud.google.com", "firebase.google.com")),
    "aws": ("P4", ("aws.amazon.com", "docs.aws.amazon.com")),
    "azure": ("P5", ("azure.microsoft.com", "learn.microsoft.com", "www.microsoft.com")),
    "oracle": ("P6", ("oracle.com", "www.oracle.com", "docs.oracle.com")),
    # F005, relocated behind the F008 S3 seam. NOT one of the six F008 providers.
    "cloudflare": (None, ("developers.cloudflare.com", "www.cloudflare.com")),
}


def declared_sources(provider: str) -> set[str]:
    """Source ids the provider's shipped config actually declares."""

    path = REPO / "config" / "examples" / "providers" / f"{provider}.example.yaml"
    if not path.is_file():
        return set()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(s.get("id")) for s in cfg.get("sources", []) if isinstance(s, dict)}


def verdict(facts: dict[str, object]) -> str:
    behaviours: tuple[str, ...] = ()
    if facts.get("exhaustion_behaviour"):
        behaviours = (str(facts["exhaustion_behaviour"]),)
    return classify(
        OfferFacts(
            offer_type=str(facts.get("offer_type")),
            requires_card=facts.get("requires_card"),  # type: ignore[arg-type]
            has_paid_dependencies=facts.get("has_paid_dependencies"),  # type: ignore[arg-type]
            exhaustion_behaviours=behaviours,
        )
    ).zero_cost_class


rows: list[dict[str, object]] = []
for provider, (slice_label, domains) in PROVIDERS.items():
    declared = declared_sources(provider)
    for case in available_cases(provider, "html"):
        try:
            fixture = load_case(provider, "html", case)
            adapter = build_fixture_adapter(fixture, official_domains=domains)
            candidates = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
        except Exception as exc:  # noqa: BLE001
            print(f"  SKIP {provider}/{case}: {type(exc).__name__}")
            continue
        for index, candidate in enumerate(candidates):
            facts = dict(candidate.facts)
            if facts.get("error") or not facts.get("offer_type"):
                continue
            rows.append(
                {
                    "provider": provider,
                    "slice": slice_label,
                    "case": case,
                    "declared_source": case in declared,
                    "profile": fixture.profile,
                    "index": index,
                    "service": facts.get("service"),
                    "offer_type": facts.get("offer_type"),
                    "verdict": verdict(facts),
                }
            )

z0 = [r for r in rows if r["verdict"] == "Z0_TRUE_FREE"]
z0_real = [r for r in z0 if r["declared_source"]]
z0_perpetual = [r for r in z0 if r["offer_type"] == "always_free"]
z0_real_perpetual = [r for r in z0_real if r["offer_type"] == "always_free"]

print("\n" + "=" * 78)
print("EVERY Z0_TRUE_FREE CANDIDATE IN THE REPOSITORY")
print("=" * 78)
print(f"{'prov':<11}{'slice':<7}{'declared':<10}{'case':<32}{'offer_type':<14}service")
print("-" * 110)
for r in sorted(z0, key=lambda x: (str(x["provider"]), str(x["case"]))):
    print(
        f"{r['provider']:<11}{str(r['slice'] or '-'):<7}{str(r['declared_source']):<10}"
        f"{str(r['case']):<32}{str(r['offer_type']):<14}{r['service']}"
    )

print("\n" + "=" * 78)
print("COUNTS AT EVERY GRANULARITY A CORRECTED SENTENCE MIGHT USE")
print("=" * 78)


def uniq(rs: list[dict[str, object]], *keys: str) -> int:
    return len({tuple(str(r[k]) for k in keys) for r in rs})


print(f"  Z0 candidates, all fixture cases ............... {len(z0)}")
print(f"  Z0 candidates, DECLARED sources only .......... {len(z0_real)}")
print(f"  Z0 candidates, perpetual (always_free) ........ {len(z0_perpetual)}")
print(f"  Z0, DECLARED + perpetual ...................... {len(z0_real_perpetual)}")
print(f"  distinct (provider, profile), declared ........ {uniq(z0_real, 'provider', 'profile')}")
print(f"  distinct (provider, service), declared ........ {uniq(z0_real, 'provider', 'service')}")
print(f"  distinct providers with a Z0 offer ............ {uniq(z0_real, 'provider')}")
print(f"    -> {sorted({str(r['provider']) for r in z0_real})}")

f008 = [r for r in z0_real if r["slice"]]
print("\n  Restricted to the SIX F008 providers (P1-P6):")
print(f"    Z0 candidates ............................... {len(f008)}")
print(f"    distinct (provider, service) ................ {uniq(f008, 'provider', 'service')}")
print(f"    distinct providers .......................... {uniq(f008, 'provider')}")
print(f"      -> {sorted({str(r['provider']) for r in f008})}")

print("\n" + "=" * 78)
print("PROVIDERS WITH A PERPETUAL (always_free) OFFER AT ALL, AND ITS BEST VERDICT")
print("=" * 78)
perp = [r for r in rows if r["offer_type"] == "always_free" and r["declared_source"]]
order = ["Z0_TRUE_FREE", "Z2_TEMPORARY_OR_CONDITIONAL", "UNKNOWN", "Z1_BILLING_EXPOSURE"]
for provider in PROVIDERS:
    mine = [r for r in perp if r["provider"] == provider]
    if not mine:
        print(f"  {provider:<11} no declared perpetual offer")
        continue
    verdicts = {str(r["verdict"]) for r in mine}
    best = next((v for v in order if v in verdicts), sorted(verdicts)[0])
    label = PROVIDERS[provider][0] or "F005"
    print(f"  {provider:<11} slice={label:<5} perpetual_offers={len(mine):<3} best={best}")

print("\nTOTAL candidates classified:", len(rows))
