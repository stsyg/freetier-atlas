"""Every declared PERPETUAL (always_free) offer in the repository, with its
verdict and the exact conditions that decide it.

This is the table a corrected sentence has to be true of. The original claim
generalised from three providers; this probe shows what the full data set
actually says, including which conditions are quoted and which are absent.
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

PROVIDERS = {
    "github": ("P1", ("docs.github.com", "github.com")),
    "vercel": ("P2", ("vercel.com",)),
    "gcp": ("P3", ("cloud.google.com", "firebase.google.com")),
    "aws": ("P4", ("aws.amazon.com", "docs.aws.amazon.com")),
    "azure": ("P5", ("azure.microsoft.com", "learn.microsoft.com", "www.microsoft.com")),
    "oracle": ("P6", ("oracle.com", "www.oracle.com", "docs.oracle.com")),
    "cloudflare": ("F005", ("developers.cloudflare.com", "www.cloudflare.com")),
}


def declared(provider: str) -> set[str]:
    path = REPO / "config" / "examples" / "providers" / f"{provider}.example.yaml"
    if not path.is_file():
        return set()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(s.get("id")) for s in cfg.get("sources", []) if isinstance(s, dict)}


print(f"{'prov':<11}{'sl':<6}{'service':<34}{'card':<7}{'paid':<7}{'exhaust':<20}verdict")
print("-" * 118)

totals: dict[str, int] = {}
for provider, (label, domains) in PROVIDERS.items():
    ids = declared(provider)
    for case in sorted(available_cases(provider, "html")):
        if case not in ids:
            continue
        try:
            fixture = load_case(provider, "html", case)
            adapter = build_fixture_adapter(fixture, official_domains=domains)
            cands = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
        except Exception:  # noqa: BLE001
            continue
        for cand in cands:
            f = dict(cand.facts)
            if f.get("offer_type") != "always_free":
                continue
            behaviours: tuple[str, ...] = ()
            if f.get("exhaustion_behaviour"):
                behaviours = (str(f["exhaustion_behaviour"]),)
            res = classify(
                OfferFacts(
                    offer_type="always_free",
                    requires_card=f.get("requires_card"),  # type: ignore[arg-type]
                    has_paid_dependencies=f.get("has_paid_dependencies"),  # type: ignore[arg-type]
                    exhaustion_behaviours=behaviours,
                )
            )
            totals[res.zero_cost_class] = totals.get(res.zero_cost_class, 0) + 1
            print(
                f"{provider:<11}{label:<6}{str(f.get('service'))[:33]:<34}"
                f"{str(f.get('requires_card')):<7}{str(f.get('has_paid_dependencies')):<7}"
                f"{str(f.get('exhaustion_behaviour')):<20}{res.zero_cost_class}"
            )
            for reason in res.blocking_conditions:
                print(f"{'':<11}{'':<6}   blocked-by: {reason}")

print("\nPERPETUAL OFFER VERDICT TOTALS (declared sources only):")
for cls, n in sorted(totals.items()):
    print(f"  {cls:<28} {n}")
print(f"  {'TOTAL':<28} {sum(totals.values())}")
