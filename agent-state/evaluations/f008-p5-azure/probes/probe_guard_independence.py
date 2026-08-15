"""Level-2 evaluator probe: are the Cosmos DB guards independent and non-vacuous?

The two decisive Cosmos facts pin DIFFERENT blocks, so deleting either must
reject the document -- and each guard must fail for its OWN reason rather than
both tripping the same way for the same cause.

Also reproduces:
  * the App Service ``APP_SERVICE_QUOTA_STOP`` weakened-profile degradation, and
  * the positive control's load-bearing property (emptying SAFE_EXHAUSTION).

Run with the PR checkout's repo root as argv[1].
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO))

from app.classify import engine  # noqa: E402
from app.classify.engine import OfferFacts, classify  # noqa: E402
from app.ingest import resolve_profile  # noqa: E402

from tests.support.fixtures import build_fixture_adapter, load_case  # noqa: E402

DOMAINS = ("azure.microsoft.com", "learn.microsoft.com")
PERPETUITY = "Free tier lasts indefinitely"
OVERAGE = "billed at regular price"


def blocks_containing(source: str, tag: str, needle: str) -> list[str]:
    return [
        m.group(0)
        for m in re.finditer(rf"<{tag}[^>]*>.*?</{tag}>", source, re.S)
        if needle in " ".join(re.sub(r"<[^>]+>", " ", m.group(0)).split())
    ]


def extract(fixture, body: bytes):
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=body)
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    return candidate


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def main() -> None:
    fixture = load_case("azure", "html", "azure-cosmos-db-free-tier")
    source = fixture.source_path.read_text(encoding="utf-8")

    print("=" * 78)
    print("1. BLOCK STRUCTURE: do the two decisive facts share a block?")
    print("=" * 78)
    perp = blocks_containing(source, "p", PERPETUITY)
    over = blocks_containing(source, "p", OVERAGE)
    print(f"  <p> blocks containing {PERPETUITY!r}: {len(perp)}")
    print(f"  <p> blocks containing {OVERAGE!r}: {len(over)}")
    same = perp and over and perp[0] == over[0]
    print(f"  SAME <p> element? {bool(same)}")

    def norm(b: str) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", b).split())

    print(f"  perpetuity block sha256(12) = {sha12(norm(perp[0]))}")
    print(f"  overage    block sha256(12) = {sha12(norm(over[0]))}")
    print()
    print("  Does the OVERAGE block also grant the allowance (25 GB / 1000 RU/s)?")
    grants = ("25 GB" in norm(over[0])) and ("1000 RU/s" in norm(over[0]))
    print(f"    -> {grants}")
    print(
        f'  So "the block that GRANTS THE ALLOWANCE also says billed at regular price" = {grants}'
    )
    print(
        f'     "the block that STATES PERPETUITY also says billed at regular price" = {bool(same)}'
    )

    # -------------------------------------------------------------- guards --
    print()
    print("=" * 78)
    print("2. GUARD INDEPENDENCE: delete each block, measure the failure reason")
    print("=" * 78)

    baseline = extract(fixture, fixture.content)
    print(
        f"  BASELINE (unmutated): error={baseline.facts.get('error')!r} "
        f"offer_type={baseline.facts.get('offer_type')!r} "
        f"exhaustion={baseline.facts.get('exhaustion_behaviour')!r}"
    )
    assert baseline.facts.get("error") is None, "baseline must be GREEN before mutating"

    results = {}
    for label, needle in (("delete_overage", OVERAGE), ("delete_perpetuity", PERPETUITY)):
        found = blocks_containing(source, "p", needle)
        assert len(found) == 1, f"{label}: anchor matched {len(found)} blocks"
        print(f"\n  PATCHED LINE [{label}]: removing {norm(found[0])[:100]!r}...")
        mutated = source.replace(found[0], "")
        assert mutated != source, f"{label}: mutation did not apply"
        cand = extract(fixture, mutated.encode())
        detail = cand.facts.get("detail")
        results[label] = (cand.facts.get("error"), detail)
        print(f"    error   = {cand.facts.get('error')!r}")
        print(f"    detail  = {str(detail)[:150]!r}")
        print(f"    offer_type present = {'offer_type' in cand.facts}")
        # Prove the OTHER fact's block survived this deletion.
        other = OVERAGE if needle == PERPETUITY else PERPETUITY
        print(f"    other block ({other!r}) still in document = {other in mutated}")

    print()
    e1, d1 = results["delete_overage"]
    e2, d2 = results["delete_perpetuity"]
    print(f"  same error code?  {e1 == e2}   ({e1!r} vs {e2!r})")
    print(f"  same detail?      {d1 == d2}")
    print(f"  -> each guard fails for its OWN reason: {d1 != d2}")

    # --------------------------------------------------- app service guard --
    print()
    print("=" * 78)
    print("3. APP SERVICE: does removing the safe-stop block degrade or reject?")
    print("=" * 78)
    as_fixture = load_case("azure", "html", "azure-app-service-quotas")
    as_source = as_fixture.source_path.read_text(encoding="utf-8")
    stop = blocks_containing(as_source, "p", "the app is stopped until the quota resets")
    assert len(stop) == 1, f"stop anchor matched {len(stop)} blocks"
    as_deleted = as_source.replace(stop[0], "").encode()

    profile = resolve_profile("azure_app_service_quotas")
    real = extract(as_fixture, as_deleted)
    print(
        f"  REAL profile, block deleted    -> error={real.facts.get('error')!r} "
        f"exhaustion={real.facts.get('exhaustion_behaviour')!r}"
    )

    patched_assertions = tuple(
        dataclasses.replace(a, required=False)
        if "the app is stopped until the quota resets" in a.text
        else a
        for a in profile.assertions
    )
    patched = dataclasses.replace(profile, assertions=patched_assertions)
    disabled = [a.field for a in patched.assertions if not a.required]
    print(f"  PATCHED LINE: required=True -> False for fields {disabled}")

    from app.ingest.adapters.html import HtmlDocAdapter  # noqa: E402
    from app.ingest.fetch import FetchPolicy, FixtureFetcher  # noqa: E402

    def run(profile_, body):
        adapter = HtmlDocAdapter(
            FixtureFetcher(
                {as_fixture.source_url: (body, "text/html")},
                FetchPolicy(official_domains=DOMAINS),
            ),
            source_urls=(as_fixture.source_url,),
            profile=profile_,
            provider="azure",
        )
        (c,) = adapter.extract(adapter.canonicalize(adapter.fetch(as_fixture.source_url)))
        return c

    weakened = run(patched, as_deleted)
    print(
        f"  WEAKENED profile, block deleted -> error={weakened.facts.get('error')!r} "
        f"state={weakened.verification_state!r}"
    )
    print(f"    exhaustion_behaviour present = {'exhaustion_behaviour' in weakened.facts}")
    print(f"    offer_type = {weakened.facts.get('offer_type')!r}")
    degraded = classify(
        OfferFacts(
            offer_type=str(weakened.facts["offer_type"]),
            requires_card=weakened.facts.get("requires_card"),
            has_paid_dependencies=weakened.facts.get("has_paid_dependencies"),
            exhaustion_behaviours=(),
        )
    )
    print(f"    classifying the DEGRADED candidate -> {degraded.zero_cost_class}")
    print(f"    blocking: {list(degraded.blocking_conditions)}")

    # ------------------------------------------------------ positive control --
    print()
    print("=" * 78)
    print("4. POSITIVE CONTROL load-bearing: empty SAFE_EXHAUSTION")
    print("=" * 78)
    control_facts = OfferFacts(
        offer_type="always_free",
        requires_card=False,
        has_paid_dependencies=False,
        exhaustion_behaviours=("hard_stop",),
    )
    healthy = classify(control_facts)
    print(f"  healthy engine: control -> {healthy.zero_cost_class}")
    original = engine.SAFE_EXHAUSTION
    print(f"  PATCHED LINE: engine.SAFE_EXHAUSTION = frozenset()  (was {len(original)})")
    engine.SAFE_EXHAUSTION = frozenset()
    try:
        broken = classify(control_facts)
        print(
            f"  broken engine:  control -> {broken.zero_cost_class}  "
            f"(control FAILS: {broken.zero_cost_class != 'Z0_TRUE_FREE'})"
        )
        sweep_ok = True
        for case in (
            "azure-free-account",
            "azure-free-services",
            "azure-cosmos-db-free-tier",
            "azure-app-service-quotas",
            "azure-static-web-apps-plans",
            "azure-devops-services",
            "azure-students",
        ):
            f = load_case("azure", "html", case)
            c = extract(f, f.content)
            beh = ()
            if c.facts.get("exhaustion_behaviour"):
                beh = (str(c.facts["exhaustion_behaviour"]),)
            r = classify(
                OfferFacts(
                    offer_type=str(c.facts["offer_type"]),
                    requires_card=c.facts.get("requires_card"),
                    has_paid_dependencies=c.facts.get("has_paid_dependencies"),
                    exhaustion_behaviours=beh,
                )
            )
            if r.zero_cost_class == "Z0_TRUE_FREE":
                sweep_ok = False
            print(f"    {case:32} -> {r.zero_cost_class}")
        print(f"  SWEEP still passes under broken engine: {sweep_ok}")
        print(
            "  ASYMMETRY PROVEN: control fails, sweep survives -> "
            f"{broken.zero_cost_class != 'Z0_TRUE_FREE' and sweep_ok}"
        )
    finally:
        engine.SAFE_EXHAUSTION = original
    print(f"  RESTORED: len(SAFE_EXHAUSTION) = {len(engine.SAFE_EXHAUSTION)}")


if __name__ == "__main__":
    main()
