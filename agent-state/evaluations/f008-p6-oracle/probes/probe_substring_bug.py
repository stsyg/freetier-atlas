"""Confirm the substring-vs-exact-set bug was REAL and the fix is correct.

The builder reported that collecting blocking conditions by substring reported
GitHub and Vercel as card-BLOCKED, because gate 4's

    "Whether a payment card is required is unknown."

CONTAINS gate 3's

    "A payment card is required."

as a substring. That is the stated-versus-unstated distinction -- the whole point
of the absence/quotation reporting -- reappearing as a string-matching bug. This
probe runs BOTH implementations over every perpetual offer and shows they
disagree, so the fix is load-bearing rather than cosmetic.
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
    "github": ("docs.github.com", "github.com"),
    "vercel": ("vercel.com",),
    "gcp": ("cloud.google.com", "firebase.google.com"),
    "aws": ("aws.amazon.com", "docs.aws.amazon.com"),
    "azure": ("azure.microsoft.com", "learn.microsoft.com", "www.microsoft.com"),
    "oracle": ("oracle.com", "www.oracle.com", "docs.oracle.com"),
    "cloudflare": ("developers.cloudflare.com", "www.cloudflare.com"),
}

CARD_GATE3 = "A payment card is required."
#: The realistic BUGGY predicate. An author collecting conditions by substring
#: writes the PHRASE, not the full sentence with its leading article and trailing
#: period -- and the phrase IS contained in gate 4's unknown-condition string.
CARD_PHRASE = "payment card is required"

by_substring: set[str] = set()
by_exact: set[str] = set()
detail: list[tuple[str, str, str]] = []

for provider, domains in PROVIDERS.items():
    path = REPO / "config" / "examples" / "providers" / f"{provider}.example.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ids = {str(s.get("id")) for s in cfg.get("sources", []) if isinstance(s, dict)}
    for case in available_cases(provider, "html"):
        if case not in ids:
            continue
        fixture = load_case(provider, "html", case)
        adapter = build_fixture_adapter(fixture, official_domains=domains)
        for cand in adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url))):
            facts = dict(cand.facts)
            if facts.get("offer_type") != "always_free":
                continue
            behaviours: tuple[str, ...] = ()
            if facts.get("exhaustion_behaviour"):
                behaviours = (str(facts["exhaustion_behaviour"]),)
            result = classify(
                OfferFacts(
                    offer_type="always_free",
                    requires_card=facts.get("requires_card"),  # type: ignore[arg-type]
                    has_paid_dependencies=facts.get("has_paid_dependencies"),  # type: ignore[arg-type]
                    exhaustion_behaviours=behaviours,
                )
            )
            conditions = set(result.blocking_conditions)
            # BUGGY: substring containment on the PHRASE.
            if any(CARD_PHRASE in c for c in conditions):
                by_substring.add(provider)
                for c in conditions:
                    if CARD_PHRASE in c and c != CARD_GATE3:
                        detail.append((provider, str(facts.get("service")), c))
            # CORRECT: exact set membership on the full condition string.
            if CARD_GATE3 in conditions:
                by_exact.add(provider)

print("Providers reported CARD-BLOCKED by each implementation:")
print(f"  by SUBSTRING (the bug)      : {sorted(by_substring)}")
print(f"  by EXACT SET  (the fix)     : {sorted(by_exact)}")
print(f"  implementations DISAGREE    : {by_substring != by_exact}")
print(f"  falsely added by substring  : {sorted(by_substring - by_exact)}")

print("\nThe exact strings that cause the false positive:")
seen: set[tuple[str, str]] = set()
for provider, service, condition in detail:
    key = (provider, condition)
    if key in seen:
        continue
    seen.add(key)
    print(f"  {provider:<11} {service[:34]:<36} {condition!r}")

print("\nWhy: gate 4's unknown-condition string CONTAINS gate 3's condition PHRASE.")
print(f"  gate 3 full : {CARD_GATE3!r}")
print(f"  gate 3 phrase: {CARD_PHRASE!r}")
print("  gate 4 full : 'Whether a payment card is required is unknown.'")
gate4 = "Whether a payment card is required is unknown."
print(f"  phrase in gate 4      : {CARD_PHRASE in gate4}")
print(f"  full sentence in gate4: {CARD_GATE3 in gate4}")
print("\nThe fix is load-bearing where an author writes the phrase; exact set membership")
print("distinguishes a STATED condition from an UNSTATED one, which is the whole point.")
