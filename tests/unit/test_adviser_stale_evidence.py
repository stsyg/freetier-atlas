"""The adviser must not propose -- or EXPORT -- a free claim whose evidence expired.

Ranked above the offer read surfaces for one reason: ``/catalogue/offers/{id}``
lets a user *find* a stale claim, ``/adviser/recommend`` actively *proposes* one,
and ``/adviser/export`` writes it into an artefact that **leaves the system** and
outlives the evidence behind it. A generated bundle is committed into someone
else's repository, where no guard of ours can ever reach it again.

Every test here is a PAIR. The stale direction proves the claim stops being
served; the fresh direction proves the very same code path still serves a
genuinely free, freshly-evidenced offer. A guard that cannot be shown to permit
is indistinguishable from one that has broken the product.

The trap this closes
--------------------
``build_candidate`` already re-runs the classify engine and marks disagreement as
a CONTRADICTION, which looks like a currency guard. It is not: it compares two
classifications of the same frozen ``material_facts``, so both sides agree
perfectly on evidence that expired years ago and nothing is excluded. The
``excluded`` assertions below pin that distinction down.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.adviser.export import ExportValidationError, build_export
from app.adviser.recommend import recommend
from app.adviser.schema import RecommendationRequest
from app.adviser.select import build_pool
from app.classify.engine import Z0_TRUE_FREE
from app.models.domain import (
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    Service,
)
from app.read_api.currency import ANCHOR_OFFER_VERSION, UNCHECKED, assess_currency

CATEGORY_ID = 1
CATEGORY_SLUG = "object-file-storage"
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

#: The refresh window the synthetic source declares.
SCHEDULE = "weekly"


def _offer(offer_id: int, slug: str) -> Offer:
    """A published, genuinely-Z0 offer. Only its evidence age ever varies."""

    provider = Provider(id=offer_id, slug=slug, name=f"{slug} Ltd", type="commercial")
    service = Service(
        id=offer_id,
        provider_id=provider.id,
        category_id=CATEGORY_ID,
        canonical_name=f"{slug} store",
        deployment_model="managed",
        portability_traits=[],
    )
    service.provider = provider
    offer = Offer(
        id=offer_id,
        service_id=service.id,
        offer_type="always_free",
        zero_cost_class=Z0_TRUE_FREE,
        status="active",
        requires_card=False,
        has_paid_dependencies=False,
        commercial_use_allowed=True,
        personal_use_allowed=True,
    )
    offer.service = service
    version = OfferVersion(
        id=offer_id,
        offer_id=offer.id,
        version_number=1,
        content_hash=f"hash-{slug}",
        offer_type="always_free",
        zero_cost_class=Z0_TRUE_FREE,
        material_facts={
            "confidence": 0.95,
            # Frozen at publish time as 1.0 -- exactly the constant that made a
            # five-year-expired claim report perfect freshness.
            "confidence_signals": {"completeness": 1.0, "freshness": 1.0},
            "classification": {"zero_cost_class": Z0_TRUE_FREE, "reasons": ["fixture"]},
            "gate": {"automatic_threshold": 0.85, "uncertain_threshold": 0.6},
        },
    )
    version.quotas = [
        Quota(
            id=offer_id,
            offer_version_id=version.id,
            metric="storage",
            amount=Decimal("100"),
            unit="GB",
            reset_period="month",
            behaviour="fixed",
            exhaustion_behaviour="hard_stop",
        )
    ]
    version.evidence = [
        Evidence(
            id=offer_id,
            source_id=offer_id,
            offer_version_id=version.id,
            snapshot_id=offer_id,
            official=True,
            url=f"https://example.invalid/{slug}",
            content_hash=f"ev-{slug}",
        )
    ]
    offer.versions = [version]
    return offer


def _pool_for(age_days: float | None, offer_id: int = 1, slug: str = "alphaco"):
    """Build a one-offer pool whose evidence is ``age_days`` old.

    ``age_days=None`` models the declaration-only shape (no fetch time at all).

    EXPIRY MECHANISM: the fixtures differ ONLY in evidence age against a fixed
    ``weekly`` window; ``NOW`` is a constant, so nothing here depends on the real
    time of day and the test cannot rot.
    """

    offer = _offer(offer_id, slug)
    version_id = offer.versions[0].id
    if age_days is None:
        verdict = UNCHECKED
    else:
        verdict = assess_currency(NOW - timedelta(days=age_days), NOW, SCHEDULE)
    return build_pool(
        [offer],
        {CATEGORY_ID: CATEGORY_SLUG},
        {},
        {(ANCHOR_OFFER_VERSION, version_id): verdict},
        as_of=NOW.date(),
    )


def _request() -> RecommendationRequest:
    return RecommendationRequest(
        workload_name="pair",
        requirements=[
            {
                "category": CATEGORY_SLUG,
                "demands": [
                    {
                        "metric": "storage",
                        "amount": Decimal("1"),
                        "unit": "GB",
                        "period": "month",
                    }
                ],
            }
        ],
    )


FRESH_DAYS = 1.0
STALE_DAYS = 1825.0  # five years, against a seven-day window


# --------------------------------------------------------------------------- #
# SURFACE 1 -- the selection pool that every adviser endpoint reads            #
# --------------------------------------------------------------------------- #


def test_pool_stale_evidence_leaves_z0() -> None:
    pool = _pool_for(STALE_DAYS)
    assert pool.z0 == ()
    assert [c.provider_slug for c in pool.stale] == ["alphaco"]
    # NOT dropped: the offer survives with its reason, so nothing is suppressed.
    assert pool.stale[0].zero_cost_class == Z0_TRUE_FREE
    assert pool.stale[0].evidence_currency.stale is True


def test_pool_fresh_evidence_still_enters_z0() -> None:
    pool = _pool_for(FRESH_DAYS)
    assert [c.provider_slug for c in pool.z0] == ["alphaco"]
    assert pool.stale == ()


def test_pool_unchecked_evidence_is_held_back_like_stale() -> None:
    """A free claim with no checkable fetch time cannot guarantee $0 either."""

    pool = _pool_for(None)
    assert pool.z0 == ()
    assert len(pool.stale) == 1
    assert pool.stale[0].evidence_currency.checked is False


def test_the_classify_cross_check_does_not_catch_this() -> None:
    """The trap, pinned: CONTRADICTION never fires on expired evidence.

    Both sides of the cross-check read the same frozen facts and agree, so
    ``excluded`` stays empty in BOTH directions. Anyone reading the cross-check
    as currency coverage is reading a guard that cannot fire here.
    """

    assert _pool_for(STALE_DAYS).excluded == ()
    assert _pool_for(FRESH_DAYS).excluded == ()


def test_confidence_is_not_reported_high_on_expired_evidence() -> None:
    assert _pool_for(FRESH_DAYS).z0[0].confidence_label == "high"
    assert _pool_for(STALE_DAYS).stale[0].confidence_label == "unknown"


# --------------------------------------------------------------------------- #
# SURFACE 2 -- /adviser/recommend                                             #
# --------------------------------------------------------------------------- #


def test_recommend_refuses_to_guarantee_zero_cost_on_expired_evidence() -> None:
    result = recommend(_request(), _pool_for(STALE_DAYS))

    assert result.fully_zero_cost is False
    assert result.components == ()
    assert len(result.impossible) == 1

    resolution = result.impossible[0]
    # The refusal is EXPLAINED, and the offer is still shown as the closest
    # candidate -- a silently-vanishing offer would be its own defect.
    assert "no longer known to be current" in resolution.blocking_reason
    assert "1825 days" in resolution.blocking_reason
    assert "7 days refresh window" in resolution.blocking_reason
    assert resolution.closest_candidate is not None
    assert resolution.closest_candidate.provider_slug == "alphaco"


def test_recommend_still_guarantees_zero_cost_on_fresh_evidence() -> None:
    result = recommend(_request(), _pool_for(FRESH_DAYS))

    assert result.fully_zero_cost is True
    assert [c.candidate.provider_slug for c in result.components] == ["alphaco"]
    assert result.impossible == ()


# --------------------------------------------------------------------------- #
# SURFACE 3 -- /adviser/export, the artefact that LEAVES THE SYSTEM            #
# --------------------------------------------------------------------------- #


def test_export_bundle_carries_no_zero_cost_guarantee_on_expired_evidence() -> None:
    """The artefact that leaves the system must not assert a $0 proof.

    NOTE ON MECHANISM. This does *not* go through
    :func:`validate_evidence_currency`. By the time ``build_export`` is called,
    ``recommend`` has already moved the expired offer out of ``components``, so
    the validator has nothing to reject and the bundle is generated with an empty
    architecture. That is the better outcome -- the user still receives a bundle
    and an explanation -- but it means the export is protected here by the
    *selection* gate, and the validator is the independent second gate exercised
    by :func:`test_export_fails_closed_even_if_the_pool_gate_were_bypassed`.
    Asserting a raise here would have tested a mechanism that never runs.
    """

    export = build_export(recommend(_request(), _pool_for(STALE_DAYS)))
    manifest = next(f.content for f in export.files if f.path == "MANIFEST.json")

    # The machine-readable artefact carries no free claim at all.
    assert export.manifest.fully_zero_cost is False
    assert json.loads(manifest)["architecture"] == []
    assert Z0_TRUE_FREE not in manifest

    # The human-readable artefact mentions the class ONLY to warn about it.
    readme = next(f.content for f in export.files if f.path == "README.md")
    z0_lines = [ln for ln in readme.splitlines() if Z0_TRUE_FREE in ln]
    assert z0_lines, "the bundle should still tell the reader the offer exists"
    for line in z0_lines:
        assert "cannot back a guaranteed-$0 architecture" in line
        assert "no longer known to be current" in line
    assert "$0 proof" not in readme


def test_export_still_writes_a_bundle_backed_by_fresh_evidence() -> None:
    export = build_export(recommend(_request(), _pool_for(FRESH_DAYS)))

    assert export.manifest.fully_zero_cost is True
    assert sorted(f.path for f in export.files) == [
        ".env.example",
        "MANIFEST.json",
        "README.md",
        "docker-compose.yml",
    ]
    manifest = json.loads(next(f.content for f in export.files if f.path == "MANIFEST.json"))
    assert [c["zero_cost_class"] for c in manifest["architecture"]] == [Z0_TRUE_FREE]
    assert "$0 proof" in next(f.content for f in export.files if f.path == "README.md")


def test_export_fails_closed_even_if_the_pool_gate_were_bypassed() -> None:
    """Defence in depth for the only path whose output we can never recall.

    A ``RecommendationResult`` constructed directly -- by a future caller, a
    regression in ``build_pool``, or a pool built with no clock -- must still be
    refused at the export boundary. This deliberately reaches past the
    selection gate to prove the second gate is independent of the first.
    """

    fresh = recommend(_request(), _pool_for(FRESH_DAYS))
    assert fresh.components, "fixture floor: expected a component to smuggle"

    smuggled = fresh.components[0]
    object.__setattr__(smuggled.candidate, "evidence_currency", UNCHECKED)

    with pytest.raises(ExportValidationError, match="cannot be exported"):
        build_export(fresh)


def test_export_error_does_not_echo_bundle_content() -> None:
    """The 422 surfaced to a caller must not leak generated file bodies."""

    fresh = recommend(_request(), _pool_for(FRESH_DAYS))
    object.__setattr__(fresh.components[0].candidate, "evidence_currency", UNCHECKED)
    with pytest.raises(ExportValidationError) as excinfo:
        build_export(fresh)
    message = str(excinfo.value)
    assert "services:" not in message
    assert "healthcheck" not in message
    assert "image:" not in message
