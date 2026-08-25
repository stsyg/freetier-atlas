"""Test support: build transient (session-free) ORM offers for the adviser.

The adviser corpus and several unit tests need a *catalogue* to recommend over
without a live database. This helper turns a plain JSON/dict description of
synthetic providers/services/offers into in-memory
:class:`~app.models.domain.Offer` graphs plus the ``category_slugs`` and
``region_index`` maps that :func:`app.adviser.select.build_pool` consumes.

Everything here is **fixture-only**: the objects are transient (never added to a
session, never persisted), so running the real stack never sees this data. The
offers are still classified by the real classify engine inside ``build_pool``,
so the Z0-safety cross-check is exercised exactly as in production.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.adviser.select import CandidatePool, build_pool
from app.models.domain import (
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    RegionAvailability,
    Service,
)
from app.read_api.currency import (
    ANCHOR_OFFER_VERSION,
    UNCHECKED,
    EvidenceCurrency,
    assess_currency,
)

#: Refresh window a synthetic source is assumed to declare when a case does not
#: say. Matches ``app.ingest.reconcile.DEFAULT_STALENESS_WINDOW``.
DEFAULT_SYNTHETIC_SCHEDULE = "weekly"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _build_quota(data: Mapping[str, Any]) -> Quota:
    return Quota(
        metric=data["metric"],
        amount=_decimal(data.get("amount")),
        unit=data.get("unit"),
        reset_period=data.get("reset_period"),
        scope=data.get("scope"),
        region_scope=data.get("region_scope"),
        behaviour=data.get("behaviour", "unknown"),
        exhaustion_behaviour=data["exhaustion_behaviour"],
    )


def _build_evidence(data: Mapping[str, Any], evidence_id: int) -> Evidence:
    ev = Evidence(
        source_id=0,
        snapshot_id=0,
        official=bool(data.get("official", False)),
        url=data.get("url"),
        title=data.get("title"),
        content_hash=data.get("content_hash", "synthetic"),
    )
    ev.id = evidence_id
    return ev


class _Catalogue:
    """The built catalogue: offers plus the maps ``build_pool`` needs."""

    def __init__(
        self,
        offers: list[Offer],
        category_slugs: dict[int, str],
        region_index: dict[tuple[int | None, int | None], list[object]],
        currency_index: dict[tuple[str, int], EvidenceCurrency] | None = None,
    ) -> None:
        self.offers = offers
        self.category_slugs = category_slugs
        self.region_index = region_index
        self.currency_index = currency_index or {}

    def pool(self) -> CandidatePool:
        return build_pool(self.offers, self.category_slugs, self.region_index, self.currency_index)


def _version_currency(version_data: Mapping[str, Any], now: datetime) -> EvidenceCurrency:
    """The evidence-currency verdict a synthetic published version stands on.

    A synthetic *published* version models an offer that cleared the real
    publication gate, and that gate makes ``evidence_backed`` and ``fresh`` hard
    conditions -- so the fixture default is evidence fetched **just now**. This
    assumption is stated here, in the fixture, rather than defaulted inside
    :func:`app.adviser.select.build_pool`, which stays fail-closed: a production
    caller that supplies no index gets ``UNCHECKED``, never "current".

    A case opts into expired evidence with ``evidence_age_days`` (and optionally
    ``evidence_schedule``), so staleness is exercised through the same code path
    production uses instead of a special-cased test double.
    """

    age_days = version_data.get("evidence_age_days", 0)
    schedule = version_data.get("evidence_schedule", DEFAULT_SYNTHETIC_SCHEDULE)
    if age_days is None:
        # Explicitly "no checkable fetch time" -- the declaration-only shape.
        return UNCHECKED
    return assess_currency(now - timedelta(days=float(age_days)), now, schedule)


def build_catalogue(data: Mapping[str, Any]) -> _Catalogue:
    """Build a transient catalogue from a ``{"providers": [...]}`` description."""

    offers: list[Offer] = []
    category_slugs: dict[int, str] = {}
    region_index: dict[tuple[int | None, int | None], list[object]] = {}
    currency_index: dict[tuple[str, int], EvidenceCurrency] = {}
    slug_to_category_id: dict[str, int] = {}
    next_category_id = 1
    evidence_counter = 1
    now = datetime.now(UTC)

    for provider_data in data.get("providers", []):
        provider = Provider(
            slug=provider_data["slug"],
            name=provider_data["name"],
            type=provider_data.get("type", "commercial"),
        )
        provider.id = provider_data["id"]

        for service_data in provider_data.get("services", []):
            category_slug = service_data.get("category_slug")
            category_id: int | None = None
            if category_slug is not None:
                if category_slug not in slug_to_category_id:
                    slug_to_category_id[category_slug] = next_category_id
                    category_slugs[next_category_id] = category_slug
                    next_category_id += 1
                category_id = slug_to_category_id[category_slug]

            service = Service(
                canonical_name=service_data["canonical_name"],
                deployment_model=service_data.get("deployment_model", "managed"),
                portability_traits=list(service_data.get("portability_traits", [])),
            )
            service.id = service_data["id"]
            service.provider_id = provider.id
            service.category_id = category_id
            service.provider = provider

            for offer_data in service_data.get("offers", []):
                offer = Offer(
                    offer_type=offer_data.get("offer_type", "recurring_quota"),
                    zero_cost_class=offer_data["zero_cost_class"],
                    commercial_use_allowed=offer_data.get("commercial_use_allowed"),
                    personal_use_allowed=offer_data.get("personal_use_allowed"),
                    requires_card=offer_data.get("requires_card"),
                    has_paid_dependencies=offer_data.get("has_paid_dependencies"),
                    eligibility=offer_data.get("eligibility"),
                )
                offer.id = offer_data["id"]
                offer.service_id = service.id
                offer.service = service

                version_data = offer_data.get("version")
                if version_data is None:
                    # No version -> unpublished; must never be selected.
                    offer.versions = []
                    offers.append(offer)
                    continue

                version = OfferVersion(
                    version_number=version_data.get("version_number", 1),
                    content_hash=version_data.get("content_hash", "synthetic"),
                    offer_type=offer.offer_type,
                    zero_cost_class=offer.zero_cost_class,
                    material_facts=dict(version_data.get("material_facts", {})),
                )
                version.id = offer_data["id"] * 1000 + version.version_number
                version.offer_id = offer.id
                version.offer = offer
                version.quotas = [_build_quota(q) for q in version_data.get("quotas", [])]
                evidence_rows: list[Evidence] = []
                for ev_data in version_data.get("evidence", []):
                    evidence_rows.append(_build_evidence(ev_data, evidence_counter))
                    evidence_counter += 1
                version.evidence = evidence_rows
                offer.versions = [version]
                currency_index[(ANCHOR_OFFER_VERSION, version.id)] = _version_currency(
                    version_data, now
                )

                for region_data in offer_data.get("regions", []):
                    row = RegionAvailability(
                        region_code=region_data["region_code"],
                        free_available=bool(region_data.get("free_available", False)),
                        residency=region_data.get("residency"),
                    )
                    row.provider_id = provider.id
                    row.offer_id = offer.id
                    region_index.setdefault((provider.id, offer.id), []).append(row)

                offers.append(offer)

    return _Catalogue(offers, category_slugs, region_index, currency_index)


def build_pool_from(data: Mapping[str, Any]) -> CandidatePool:
    """Convenience: build a catalogue and return its :class:`CandidatePool`."""

    return build_catalogue(data).pool()


__all__: Sequence[str] = ("build_catalogue", "build_pool_from")
