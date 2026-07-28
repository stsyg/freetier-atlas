"""Idempotent declarative-config -> database synchronisation (F005 slice 1).

Turns a validated provider configuration (``app.config.models.ProviderConfig``,
loaded from ``config/examples/providers/<provider>.yaml`` via
:func:`app.config.loader.load_and_validate`) into the ORM ``Provider`` and
``Source`` rows the ingestion pipeline scans.

The YAML config and the database use different field names for the same
concepts; this module is the single place that bridges them
(docs/PROVIDER_ADAPTERS.md):

======================  ==============================
YAML (config.models)    database (models.domain.Source)
======================  ==============================
``source.id``           ``slug``   (the idempotent-sync key)
``source.type``         ``adapter_type``
``source.url``          ``endpoint``
``source.extraction_profile``  ``parser_profile``
``source.schedule_ref`` ``schedule``
``source.trust_level``  ``trust_level`` (+ derived ``official`` flag)
======================  ==============================

Idempotency contract: the sync upserts on a *stable key* -- ``Provider.slug``
for the provider and ``Source.slug`` for each source (both UNIQUE). A second run
against a byte-identical config therefore matches the existing rows, changes
nothing, and reports zero created/updated rows. There is **no publication
path**: this module only ever writes ``provider`` / ``source`` rows, the
declared ``service.category_id`` (F008 slice S1, see
:func:`categorise_services`) and the declared ``provider_category_coverage``
rows (F008 slice S2, see :func:`sync_coverage`); it never touches ``offer`` /
``offer_version`` / ``quota`` and opens no socket.

The caller owns the transaction: :func:`sync_provider` uses ``session.flush()``
(so the new provider id is available for its sources) but never commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.models import ProviderConfig
from app.config.models import Source as SourceConfig
from app.ingest.trust import OFFICIAL_TRUST_LEVEL
from app.models.domain import (
    Category,
    Provider,
    ProviderCategoryCoverage,
    Service,
    Source,
)

#: ``Provider.type`` is required infrastructure metadata with no closed
#: vocabulary and no counterpart in the provider config schema. Until the config
#: gains an explicit provider type this bridge records a neutral default; it is
#: never an offer fact, so "unknown is better than guessed" does not apply.
DEFAULT_PROVIDER_TYPE = "cloud"


@dataclass(frozen=True)
class SourceSyncOutcome:
    """The result of syncing one configured source."""

    slug: str
    action: str  # "created" | "updated" | "unchanged"
    source_id: int | None = None


@dataclass
class SyncResult:
    """A summary of one :func:`sync_provider` run (for idempotency assertions)."""

    provider_slug: str
    provider_id: int | None = None
    provider_action: str = "unchanged"  # "created" | "updated" | "unchanged"
    sources: list[SourceSyncOutcome] = field(default_factory=list)
    categorisation: CategorisationResult | None = None
    coverage: CoverageSyncResult | None = None

    @property
    def created(self) -> int:
        return sum(1 for s in self.sources if s.action == "created")

    @property
    def updated(self) -> int:
        return sum(1 for s in self.sources if s.action == "updated")

    @property
    def unchanged(self) -> int:
        return sum(1 for s in self.sources if s.action == "unchanged")

    @property
    def categorised(self) -> int:
        """How many services this run assigned a declared category to."""

        return self.categorisation.updated if self.categorisation is not None else 0

    @property
    def changed(self) -> bool:
        """True when this run created/updated the provider, a source, a category
        or a coverage declaration."""

        return (
            self.provider_action != "unchanged"
            or self.created > 0
            or self.updated > 0
            or self.categorised > 0
            or (self.coverage is not None and self.coverage.changed)
        )


@dataclass(frozen=True)
class ServiceCategorisation:
    """The result of applying one declared ``service -> category`` mapping."""

    canonical_name: str
    category_slug: str
    #: "updated"          -- the service now carries the declared category
    #: "unchanged"        -- it already did (idempotent re-run)
    #: "unknown_service"  -- no such service for this provider (yet); no-op
    #: "unknown_category" -- the category row is not seeded (pre-0010 DB); no-op
    action: str
    service_id: int | None = None


@dataclass
class CategorisationResult:
    """A summary of one :func:`categorise_services` run."""

    provider_slug: str
    outcomes: list[ServiceCategorisation] = field(default_factory=list)

    def _count(self, action: str) -> int:
        return sum(1 for o in self.outcomes if o.action == action)

    @property
    def updated(self) -> int:
        return self._count("updated")

    @property
    def unchanged(self) -> int:
        return self._count("unchanged")

    @property
    def unknown_services(self) -> int:
        return self._count("unknown_service")

    @property
    def unknown_categories(self) -> int:
        return self._count("unknown_category")

    @property
    def withdrawn(self) -> int:
        """Services whose declaration was removed and which reverted to NULL."""

        return self._count("withdrawn")

    @property
    def changed(self) -> bool:
        return self.updated > 0 or self.withdrawn > 0


@dataclass(frozen=True)
class CoverageDeclarationOutcome:
    """The result of syncing one declared provider x category coverage state."""

    category_slug: str
    #: "created"          -- the declaration is now persisted
    #: "updated"          -- an existing declaration converged to the new YAML
    #: "unchanged"        -- byte-identical to what is stored (idempotent re-run)
    #: "withdrawn"        -- a stored row the YAML no longer declares was removed
    #: "unknown_category" -- the category row is not seeded (pre-0010 DB); no-op
    #: "unknown_source"   -- the referenced source row does not exist yet; no-op
    action: str
    state: str | None = None
    coverage_id: int | None = None


@dataclass
class CoverageSyncResult:
    """A summary of one :func:`sync_coverage` run."""

    provider_slug: str
    outcomes: list[CoverageDeclarationOutcome] = field(default_factory=list)

    def _count(self, action: str) -> int:
        return sum(1 for o in self.outcomes if o.action == action)

    @property
    def created(self) -> int:
        return self._count("created")

    @property
    def updated(self) -> int:
        return self._count("updated")

    @property
    def unchanged(self) -> int:
        return self._count("unchanged")

    @property
    def withdrawn(self) -> int:
        return self._count("withdrawn")

    @property
    def unknown_categories(self) -> int:
        return self._count("unknown_category")

    @property
    def unknown_sources(self) -> int:
        return self._count("unknown_source")

    @property
    def declared(self) -> int:
        """Rows this run left in place as declarations (created + updated + unchanged)."""

        return self.created + self.updated + self.unchanged

    @property
    def changed(self) -> bool:
        return self.created > 0 or self.updated > 0 or self.withdrawn > 0


def _desired_source_fields(config: SourceConfig, provider_id: int) -> dict[str, object]:
    """Bridge one YAML source into the ORM column values it maps to."""

    return {
        "provider_id": provider_id,
        "adapter_type": config.type,
        "trust_level": config.trust_level,
        "official": config.trust_level == OFFICIAL_TRUST_LEVEL,
        "endpoint": config.url,
        "schedule": config.schedule_ref,
        "parser_profile": config.extraction_profile,
        "enabled": True,
    }


def _sync_provider_row(session: Session, config: ProviderConfig) -> tuple[Provider, str]:
    section = config.provider
    domains = list(section.official_domains)
    existing = session.execute(
        select(Provider).where(Provider.slug == section.id)
    ).scalar_one_or_none()

    if existing is None:
        provider = Provider(
            slug=section.id,
            name=section.name,
            type=DEFAULT_PROVIDER_TYPE,
            official_domains=domains,
        )
        session.add(provider)
        session.flush()
        return provider, "created"

    changed = False
    if existing.name != section.name:
        existing.name = section.name
        changed = True
    if list(existing.official_domains or []) != domains:
        existing.official_domains = domains
        changed = True
    session.flush()
    return existing, ("updated" if changed else "unchanged")


def _sync_source_row(session: Session, config: SourceConfig, provider_id: int) -> SourceSyncOutcome:
    desired = _desired_source_fields(config, provider_id)
    existing = session.execute(select(Source).where(Source.slug == config.id)).scalar_one_or_none()

    if existing is None:
        source = Source(slug=config.id, **desired)
        session.add(source)
        session.flush()
        return SourceSyncOutcome(slug=config.id, action="created", source_id=source.id)

    changed = False
    for column, value in desired.items():
        if getattr(existing, column) != value:
            setattr(existing, column, value)
            changed = True
    session.flush()
    return SourceSyncOutcome(
        slug=config.id,
        action=("updated" if changed else "unchanged"),
        source_id=existing.id,
    )


def categorise_services(session: Session, config: ProviderConfig) -> CategorisationResult:
    """Apply the config's declared ``service_categories`` to existing services.

    Backfills ``service.category_id`` for this provider's already-persisted
    services from the **declared** mapping (F008 slice S1). Idempotent and
    slug-keyed: a second run against the same config reports zero updates.

    Nothing is ever inferred. Naming a service that does not exist -- or a
    category that is not seeded yet -- is a recorded no-op rather than an error,
    so a mapping may legitimately be declared before the service is first
    discovered.

    Declarations are **withdrawable**. Because ``service_categories`` is declared
    metadata rather than an accumulating fact, the database converges to whatever
    the YAML currently says: removing a service from the map reverts that
    service to uncategorised (``category_id IS NULL``) on the next sync instead
    of silently retaining a category the config no longer claims. Retaining it
    would leave a stale declaration nobody can see in the config -- the same
    "second source of truth" failure this feature exists to remove.

    This writes ``service.category_id`` and nothing else: it never touches
    ``offer`` / ``offer_version`` / ``quota``, and because category is absent
    from the publisher's stable material facts, it cannot change any
    ``content_hash`` or mint an ``offer_version``. The caller owns the
    transaction (this flushes but never commits).
    """

    result = CategorisationResult(provider_slug=config.provider.id)
    declared = config.service_categories

    provider = session.execute(
        select(Provider).where(Provider.slug == config.provider.id)
    ).scalar_one_or_none()
    if provider is None:
        return result

    category_ids = {
        row.slug: row.id
        for row in session.execute(
            select(Category).where(Category.slug.in_(sorted(set(declared.values()))))
        ).scalars()
    }

    for canonical_name in sorted(declared):
        slug = declared[canonical_name]
        category_id = category_ids.get(slug)
        if category_id is None:
            result.outcomes.append(ServiceCategorisation(canonical_name, slug, "unknown_category"))
            continue

        service = session.execute(
            select(Service).where(
                Service.provider_id == provider.id,
                Service.canonical_name == canonical_name,
            )
        ).scalar_one_or_none()
        if service is None:
            result.outcomes.append(ServiceCategorisation(canonical_name, slug, "unknown_service"))
            continue

        if service.category_id == category_id:
            result.outcomes.append(
                ServiceCategorisation(canonical_name, slug, "unchanged", service.id)
            )
            continue

        service.category_id = category_id
        result.outcomes.append(ServiceCategorisation(canonical_name, slug, "updated", service.id))

    # Withdrawal: any of this provider's services that carries a category the
    # config no longer declares reverts to uncategorised.
    undeclared = session.execute(
        select(Service).where(
            Service.provider_id == provider.id,
            Service.category_id.is_not(None),
            Service.canonical_name.not_in(sorted(declared)),
        )
    ).scalars()
    for service in undeclared:
        service.category_id = None
        result.outcomes.append(
            ServiceCategorisation(service.canonical_name, "", "withdrawn", service.id)
        )

    session.flush()
    return result


def sync_coverage(session: Session, config: ProviderConfig) -> CoverageSyncResult:
    """Persist the config's declared provider x category coverage (F008 slice S2).

    Upserts one ``provider_category_coverage`` row per declared canonical
    category, keyed idempotently on ``(provider_id, category_id)``. A second run
    against a byte-identical config reports zero changes.

    Declarations are the source of truth and are **withdrawable**: a state that
    changes in the YAML (e.g. ``verified_free`` -> ``unknown``) overwrites the
    stored row, and a stored row for a category the config no longer declares is
    deleted rather than left to linger as an invisible stale claim. The config
    schema requires all fourteen canonical categories, so in practice withdrawal
    only fires for rows written by an older schema or by hand.

    Nothing is derived here. The table holds the *declaration* only; the observed
    state is computed on demand by ``app.read_api.coverage`` (decision Q11), and
    a declared-vs-derived contradiction is recorded as a review item by
    ``app.ingest.reconcile_coverage`` -- never written back into this table.

    Must run after the source sync so ``coverage[...].source`` references resolve
    to real ``source.id`` values. The caller owns the transaction (this flushes
    but never commits).
    """

    result = CoverageSyncResult(provider_slug=config.provider.id)
    declared = config.coverage

    provider = session.execute(
        select(Provider).where(Provider.slug == config.provider.id)
    ).scalar_one_or_none()
    if provider is None:
        return result

    category_ids = {
        row.slug: row.id
        for row in session.execute(
            select(Category).where(Category.slug.in_(sorted(declared)))
        ).scalars()
    }
    source_ids = {
        row.slug: row.id
        for row in session.execute(
            select(Source).where(Source.provider_id == provider.id)
        ).scalars()
        if row.slug is not None
    }
    existing_rows = {
        row.category_id: row
        for row in session.execute(
            select(ProviderCategoryCoverage).where(
                ProviderCategoryCoverage.provider_id == provider.id
            )
        ).scalars()
    }

    now = datetime.now(UTC)
    declared_category_ids: set[int] = set()

    for slug in sorted(declared):
        entry = declared[slug]
        category_id = category_ids.get(slug)
        if category_id is None:
            result.outcomes.append(
                CoverageDeclarationOutcome(slug, "unknown_category", entry.state)
            )
            continue

        source_id: int | None = None
        if entry.source is not None:
            source_id = source_ids.get(entry.source)
            if source_id is None:
                # The config validator already proved the id is declared in the
                # file, so this only happens against a partially synced database.
                result.outcomes.append(
                    CoverageDeclarationOutcome(slug, "unknown_source", entry.state)
                )
                continue

        declared_category_ids.add(category_id)
        desired: dict[str, object] = {
            "state": entry.state,
            "rationale": (entry.rationale or "").strip() or None,
            "source_id": source_id,
            "evidence_url": (entry.evidence_url or "").strip() or None,
        }

        row = existing_rows.get(category_id)
        if row is None:
            row = ProviderCategoryCoverage(
                provider_id=provider.id,
                category_id=category_id,
                declared_at=now,
                **desired,
            )
            session.add(row)
            session.flush()
            result.outcomes.append(CoverageDeclarationOutcome(slug, "created", entry.state, row.id))
            continue

        changed = False
        for column, value in desired.items():
            if getattr(row, column) != value:
                setattr(row, column, value)
                changed = True
        if changed:
            row.declared_at = now
        result.outcomes.append(
            CoverageDeclarationOutcome(
                slug, ("updated" if changed else "unchanged"), entry.state, row.id
            )
        )

    for category_id, row in existing_rows.items():
        if category_id in declared_category_ids:
            continue
        coverage_id, state = row.id, row.state
        session.delete(row)
        result.outcomes.append(CoverageDeclarationOutcome("", "withdrawn", state, coverage_id))

    session.flush()
    return result


def sync_provider(session: Session, config: ProviderConfig) -> SyncResult:
    """Upsert ``config`` into ``provider`` + ``source`` rows; return a summary.

    Idempotent on ``Provider.slug`` and ``Source.slug``: re-running against the
    same config produces zero changes. After the source sync it applies the
    declared ``service_categories`` mapping (:func:`categorise_services`) and the
    declared ``coverage`` block (:func:`sync_coverage`, which runs after the
    sources so its ``source`` references resolve); both are themselves
    idempotent. The caller owns the transaction (this flushes but never commits).
    """

    provider, provider_action = _sync_provider_row(session, config)
    result = SyncResult(
        provider_slug=config.provider.id,
        provider_id=provider.id,
        provider_action=provider_action,
    )
    for source_config in config.sources:
        result.sources.append(_sync_source_row(session, source_config, provider.id))
    result.categorisation = categorise_services(session, config)
    result.coverage = sync_coverage(session, config)
    return result


__all__: Sequence[str] = (
    "DEFAULT_PROVIDER_TYPE",
    "CategorisationResult",
    "CoverageDeclarationOutcome",
    "CoverageSyncResult",
    "ServiceCategorisation",
    "SourceSyncOutcome",
    "SyncResult",
    "categorise_services",
    "sync_coverage",
    "sync_provider",
)
