"""Read-only catalogue queries (F005 slice 3).

Pure ``SELECT`` access to the *published* catalogue. Every function here reads
and never writes: no ``INSERT`` / ``UPDATE`` / ``DELETE`` is issued anywhere, so
the immutability and separation triggers are never touched.

Only *published* data is exposed. An offer is considered published when it has
at least one :class:`~app.models.domain.OfferVersion` (the S2 publisher appends
one on publish). The pre-publication ``candidate`` and quarantined
``discovery_candidate`` tables are **never** queried, so community/unofficial
data can never leak into a catalogue response. Evidence is only ever surfaced
when it is linked to a published ``offer_version``.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.domain import (
    Category,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Service,
    Snapshot,
    Source,
)


def latest_version(offer: Offer) -> OfferVersion | None:
    """Return an offer's current (highest ``version_number``) version, if any."""

    versions = list(offer.versions)
    if not versions:
        return None
    return max(versions, key=lambda v: v.version_number)


def is_published(offer: Offer) -> bool:
    """An offer is published once it has at least one immutable version."""

    return bool(offer.versions)


def fetch_providers(session: Session) -> Sequence[Provider]:
    """All providers, eager-loading services -> offers -> versions -> quotas."""

    stmt = (
        select(Provider)
        .options(
            selectinload(Provider.services)
            .selectinload(Service.offers)
            .selectinload(Offer.versions)
            .selectinload(OfferVersion.quotas)
        )
        .order_by(Provider.slug)
    )
    return list(session.execute(stmt).scalars().unique())


def fetch_provider(session: Session, slug: str) -> Provider | None:
    """One provider by slug, eager-loading its service/offer/version graph."""

    stmt = (
        select(Provider)
        .where(Provider.slug == slug)
        .options(
            selectinload(Provider.services)
            .selectinload(Service.offers)
            .selectinload(Offer.versions)
            .selectinload(OfferVersion.quotas)
        )
    )
    return session.execute(stmt).scalars().unique().one_or_none()


def fetch_offer(session: Session, offer_id: int) -> Offer | None:
    """One offer by id with its service, versions, and quotas eager-loaded."""

    stmt = (
        select(Offer)
        .where(Offer.id == offer_id)
        .options(
            selectinload(Offer.service).selectinload(Service.provider),
            selectinload(Offer.versions).selectinload(OfferVersion.quotas),
        )
    )
    return session.execute(stmt).scalars().unique().one_or_none()


def fetch_offer_evidence(session: Session, *, offer_version_id: int) -> Sequence[Evidence]:
    """Official evidence linked to a published offer version, with provenance.

    Only rows whose ``offer_version_id`` matches are returned (never candidate-
    stage evidence), and the ``source`` / ``snapshot`` provenance is eager-loaded.
    """

    stmt = (
        select(Evidence)
        .where(Evidence.offer_version_id == offer_version_id)
        .options(
            selectinload(Evidence.source),
            selectinload(Evidence.snapshot),
        )
        .order_by(Evidence.id)
    )
    return list(session.execute(stmt).scalars().unique())


def fetch_offer_versions(session: Session, *, offer_id: int) -> Sequence[OfferVersion]:
    """The full append-only version history for an offer (oldest first)."""

    stmt = (
        select(OfferVersion)
        .where(OfferVersion.offer_id == offer_id)
        .order_by(OfferVersion.version_number)
    )
    return list(session.execute(stmt).scalars().unique())


def fetch_offer_change_events(session: Session, *, offer_id: int) -> Sequence[ChangeEvent]:
    """Published change events for an offer (chronological)."""

    stmt = (
        select(ChangeEvent)
        .where(
            ChangeEvent.offer_id == offer_id,
            ChangeEvent.publication_status == "published",
        )
        .order_by(ChangeEvent.id)
    )
    return list(session.execute(stmt).scalars().unique())


def category_map(session: Session, category_ids: Sequence[int]) -> dict[int, Category]:
    """Return a ``{id: Category}`` map for the given category ids."""

    ids = [cid for cid in category_ids if cid is not None]
    if not ids:
        return {}
    stmt = select(Category).where(Category.id.in_(ids))
    return {c.id: c for c in session.execute(stmt).scalars().unique()}


def get_snapshot(session: Session, snapshot_id: int) -> Snapshot | None:
    """Fetch a snapshot by id (used only for provenance display)."""

    return session.get(Snapshot, snapshot_id)


def get_source(session: Session, source_id: int) -> Source | None:
    """Fetch a source by id (used only for provenance display)."""

    return session.get(Source, source_id)


__all__: Sequence[str] = (
    "latest_version",
    "is_published",
    "fetch_providers",
    "fetch_provider",
    "fetch_offer",
    "fetch_offer_evidence",
    "fetch_offer_versions",
    "fetch_offer_change_events",
    "category_map",
    "get_snapshot",
    "get_source",
)
