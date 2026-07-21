"""Read-only ORM adapter for the Z0 classification engine.

Bridges persisted :class:`~app.models.domain.Offer` / :class:`~app.models.domain.Quota`
rows to the pure :func:`app.classify.engine.classify` function. Performs no
database writes; it only reads attributes already loaded on the instances (or
lazily loaded by the caller's session).
"""

from __future__ import annotations

from datetime import date

from app.models.domain import Offer, OfferVersion

from .engine import ClassificationResult, OfferFacts, classify


def _select_version(offer: Offer, version: OfferVersion | None) -> OfferVersion | None:
    """Return the version to classify: the explicit one, else the latest by number."""

    if version is not None:
        return version
    versions = list(offer.versions)
    if not versions:
        return None
    return max(versions, key=lambda v: v.version_number)


def offer_facts_from_orm(offer: Offer, version: OfferVersion | None = None) -> OfferFacts:
    """Build :class:`OfferFacts` from an ``Offer`` and one of its versions.

    When ``version`` is omitted the latest version (highest ``version_number``)
    is used to source the quota exhaustion behaviours; if the offer has no
    versions, the exhaustion set is empty (which the engine treats as unknown).
    """

    selected = _select_version(offer, version)
    offer_type = selected.offer_type if selected is not None else offer.offer_type
    behaviours: tuple[str, ...] = ()
    if selected is not None:
        behaviours = tuple(quota.exhaustion_behaviour for quota in selected.quotas)

    return OfferFacts(
        offer_type=offer_type,
        requires_card=offer.requires_card,
        has_paid_dependencies=offer.has_paid_dependencies,
        exhaustion_behaviours=behaviours,
        eligibility=offer.eligibility,
        available_from=offer.available_from,
        available_until=offer.available_until,
    )


def classify_offer(
    offer: Offer,
    version: OfferVersion | None = None,
    *,
    as_of: date | None = None,
) -> ClassificationResult:
    """Classify an ORM ``Offer`` (read-only). See :func:`offer_facts_from_orm`."""

    return classify(offer_facts_from_orm(offer, version), as_of=as_of)


__all__ = ("offer_facts_from_orm", "classify_offer")
