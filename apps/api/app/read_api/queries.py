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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.domain import (
    Category,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    ProviderCategoryCoverage,
    ReviewItem,
    Service,
    Snapshot,
    Source,
)

from .currency import (
    ANCHOR_OFFER_VERSION,
    CurrencyContext,
    EvidenceCurrency,
    assess_currency,
    worst,
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


def fetch_offers_by_ids(session: Session, offer_ids: Sequence[int]) -> dict[int, Offer]:
    """Batch-fetch offers by id for compare, eager-loading the compare graph.

    Returns a ``{id: Offer}`` map (only ids that exist are present). Loads each
    offer's service -> provider, its versions -> quotas, and its versions ->
    evidence so the comparison can be serialized without N+1 queries. Publication
    is decided by the caller via :func:`is_published`; the ``candidate`` /
    ``discovery_candidate`` tables are never touched.
    """

    ids = [oid for oid in offer_ids if oid is not None]
    if not ids:
        return {}
    stmt = (
        select(Offer)
        .where(Offer.id.in_(ids))
        .options(
            selectinload(Offer.service).selectinload(Service.provider),
            selectinload(Offer.versions).selectinload(OfferVersion.quotas),
            selectinload(Offer.versions).selectinload(OfferVersion.evidence),
        )
    )
    return {offer.id: offer for offer in session.execute(stmt).scalars().unique()}


def category_map_for_providers(
    session: Session, providers: Sequence[Provider]
) -> dict[int, Category]:
    """Resolve every category referenced by ``providers`` services in one query."""

    category_ids = [
        s.category_id
        for provider in providers
        for s in provider.services
        if s.category_id is not None
    ]
    return category_map(session, category_ids)


def get_snapshot(session: Session, snapshot_id: int) -> Snapshot | None:
    """Fetch a snapshot by id (used only for provenance display)."""

    return session.get(Snapshot, snapshot_id)


def get_source(session: Session, source_id: int) -> Source | None:
    """Fetch a source by id (used only for provenance display)."""

    return session.get(Source, source_id)


# --------------------------------------------------------------------------- #
# F008 slice S2 - coverage declarations + derivation signals                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CoverageSignalContext:
    """Everything the coverage derivation needs beyond the provider graph.

    Gathered once per request in a handful of queries so the per-pair derivation
    stays a pure function over already-loaded data.
    """

    #: (provider_id, category_slug) -> the human declaration, when there is one.
    declarations: Mapping[tuple[int, str], ProviderCategoryCoverage]
    #: (provider_slug, service_canonical_name) pairs with an unresolved,
    #: *pending* evidence contradiction.
    conflicted_services: frozenset[tuple[str, str]]
    #: Published offer-version ids whose backing evidence is past its source's
    #: refresh window.
    stale_offer_version_ids: frozenset[int]


def fetch_coverage_declarations(
    session: Session, provider_ids: Sequence[int]
) -> dict[tuple[int, str], ProviderCategoryCoverage]:
    """Declared coverage for ``provider_ids``, keyed by (provider id, slug)."""

    if not provider_ids:
        return {}
    stmt = (
        select(ProviderCategoryCoverage, Category.slug)
        .join(Category, Category.id == ProviderCategoryCoverage.category_id)
        .where(ProviderCategoryCoverage.provider_id.in_(sorted(set(provider_ids))))
    )
    return {(row.provider_id, slug): row for row, slug in session.execute(stmt).all()}


def fetch_conflicted_services(session: Session) -> frozenset[tuple[str, str]]:
    """(provider, service) identities with a pending evidence contradiction.

    Only ``evidence_conflict``-flavoured review items count. Coverage-mismatch
    review items are deliberately excluded: they are *produced* from the derived
    state, so feeding them back in would make every mismatch permanently
    ``conflicting`` regardless of the evidence.
    """

    stmt = select(
        ReviewItem.evidence_conflict["identity"]["provider"].astext,
        ReviewItem.evidence_conflict["identity"]["service"].astext,
    ).where(
        ReviewItem.admin_disposition == "pending",
        ReviewItem.reason.like("evidence_conflict%"),
    )
    return frozenset(
        (provider, service)
        for provider, service in session.execute(stmt).all()
        if provider is not None and service is not None
    )


def fetch_evidence_currency(
    session: Session,
    *,
    now: datetime,
    offer_version_ids: Sequence[int] | None = None,
) -> dict[tuple[str, int], EvidenceCurrency]:
    """Currency of the evidence behind published offer versions.

    Keyed by ``(anchor_kind, anchor_id)`` -- today only
    :data:`~app.read_api.currency.ANCHOR_OFFER_VERSION`. The declaration anchor
    is deliberately absent rather than faked: a declaration backed only by an
    ``evidence_url`` has no snapshot and therefore no fetch time, so it resolves
    to :data:`~app.read_api.currency.UNCHECKED` through
    :func:`~app.read_api.currency.currency_for` instead of silently reading as
    current.

    A version resting on several sources takes its *least* current verdict
    (:func:`~app.read_api.currency.worst`): a claim is only as current as its
    stalest support.

    ``offer_version_ids`` narrows the scan to the anchors a request actually
    needs. It is an optimisation ONLY: omitting it scans every linked evidence
    row, which is the original behaviour and what the coverage matrix wants
    (it needs the whole catalogue). It never changes a verdict -- a version's
    currency depends on its own evidence alone -- so a narrowed call and a full
    call agree on every key they share. An empty sequence means "no anchors
    needed" and short-circuits to ``{}``; ``None`` means "no restriction".
    """

    if offer_version_ids is not None:
        wanted = {int(vid) for vid in offer_version_ids if vid is not None}
        if not wanted:
            return {}
    else:
        wanted = None

    stmt = (
        select(
            Evidence.offer_version_id,
            Snapshot.fetched_at,
            Source.schedule,
        )
        .join(Snapshot, Snapshot.id == Evidence.snapshot_id)
        .join(Source, Source.id == Evidence.source_id)
        .where(Evidence.offer_version_id.is_not(None))
    )
    if wanted is not None:
        stmt = stmt.where(Evidence.offer_version_id.in_(sorted(wanted)))
    per_version: dict[int, list[EvidenceCurrency]] = {}
    for offer_version_id, fetched_at, schedule in session.execute(stmt).all():
        if offer_version_id is None:
            continue
        per_version.setdefault(int(offer_version_id), []).append(
            assess_currency(fetched_at, now, schedule)
        )
    return {
        (ANCHOR_OFFER_VERSION, version_id): worst(verdicts)
        for version_id, verdicts in per_version.items()
    }


def currency_context(
    session: Session,
    *,
    now: datetime,
    offer_version_ids: Sequence[int] | None = None,
) -> CurrencyContext:
    """Build the read-time :class:`CurrencyContext` for one request.

    The single place a catalogue read surface acquires a clock. Handlers call
    this once and hand the result to the serializers, so there is exactly one
    ``now`` per response and two fields of the same payload can never be
    assessed against different moments.
    """

    return CurrencyContext(
        index=fetch_evidence_currency(session, now=now, offer_version_ids=offer_version_ids),
        now=now,
    )


def version_ids_for_offers(offers: Iterable[Offer]) -> list[int]:
    """Every offer-version id reachable from ``offers`` (for scoping a context).

    Deliberately ALL versions rather than only the latest: ``/offers/{id}/history``
    renders every version, and a scope that covered only the current one would
    silently hand the history rows :data:`UNCHECKED`. Over-scoping costs a few
    ids; under-scoping costs a wrong answer that looks deliberate.
    """

    return sorted({v.id for offer in offers for v in offer.versions if v.id is not None})


def fetch_stale_offer_version_ids(session: Session, *, now: datetime) -> frozenset[int]:
    """Published offer versions whose backing evidence is past its refresh window.

    Conservative: a version is stale when **any** of its evidence snapshots is
    older than the schedule window of the source it came from. Overstating
    staleness only ever makes the catalogue admit uncertainty, which is the
    direction the product errs in.

    Now a thin projection of :func:`fetch_evidence_currency` so the coverage
    derivation and the offer/adviser surfaces cannot drift apart on what "stale"
    means. The signature and semantics are unchanged: evidence with no
    ``fetched_at`` is still not stale (it is *unchecked*, which this particular
    caller does not distinguish), and the F008 ``/catalogue/categories`` path
    behaves exactly as before.
    """

    index = fetch_evidence_currency(session, now=now)
    return frozenset(
        anchor_id
        for (anchor_kind, anchor_id), verdict in index.items()
        if anchor_kind == ANCHOR_OFFER_VERSION and verdict.stale
    )


def coverage_signal_context(
    session: Session, providers: Sequence[Provider], *, now: datetime | None = None
) -> CoverageSignalContext:
    """Gather the declarations and derivation signals for ``providers``."""

    return CoverageSignalContext(
        declarations=fetch_coverage_declarations(session, [p.id for p in providers]),
        conflicted_services=fetch_conflicted_services(session),
        stale_offer_version_ids=fetch_stale_offer_version_ids(
            session, now=now or datetime.now(UTC)
        ),
    )


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
    "fetch_offers_by_ids",
    "category_map_for_providers",
    "get_snapshot",
    "get_source",
    "CoverageSignalContext",
    "coverage_signal_context",
    "currency_context",
    "fetch_coverage_declarations",
    "fetch_conflicted_services",
    "fetch_evidence_currency",
    "fetch_stale_offer_version_ids",
    "version_ids_for_offers",
)
