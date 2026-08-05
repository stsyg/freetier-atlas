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
(so the new provider id is available for its sources) but never commits. Within
that transaction the provider is nonetheless an **atomic unit**: all four writes
run inside a SAVEPOINT that is rolled back, and the original exception re-raised
unchanged, whenever any of them raises -- so a provider partially synced *by a
failure in those four writes* is never left in the caller's transaction for it
to commit. A failure raised while
that SAVEPOINT is being *released* is outside the guarantee, because the writes
have already joined the caller's transaction by then; see
:func:`sync_provider` for that boundary and why it is documented rather than
guarded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.models import MIN_EVIDENCE_BACKED_COVERAGE, ProviderConfig
from app.config.models import Source as SourceConfig
from app.ingest.trust import OFFICIAL_TRUST_LEVEL
from app.models.domain import (
    Category,
    Provider,
    ProviderCategoryCoverage,
    Service,
    Source,
)
from app.models.vocab import EVIDENCE_BACKED_COVERAGE_STATES

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
    #: "unknown_category" -- the declared slug matches no category row (pre-0010
    #:                       DB, or taxonomy drift such as a RENAMED slug). No
    #:                       row is written, and because the pair cannot be
    #:                       attributed to a category id the prune is suppressed
    #:                       for the whole run so no still-declared row is lost.
    #: "unknown_source"   -- the referenced source row does not exist yet. The
    #:                       declaration is PRESERVED (any stored row is left
    #:                       untouched); a reference this sync cannot resolve is
    #:                       a resolution failure, never a withdrawal.
    action: str
    state: str | None = None
    coverage_id: int | None = None


@dataclass
class CoverageSyncResult:
    """A summary of one :func:`sync_coverage` run."""

    provider_slug: str
    outcomes: list[CoverageDeclarationOutcome] = field(default_factory=list)
    prune_suppressed: bool = False
    """True when an unresolved category reference made withdrawal unattributable.

    The prune is skipped wholesale for that run rather than deleting rows it
    cannot positively prove were withdrawn.
    """

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
    def unresolved_sources(self) -> list[str]:
        """Category slugs whose declared ``source`` this run could not resolve."""

        return sorted(o.category_slug for o in self.outcomes if o.action == "unknown_source")

    @property
    def unresolved_categories(self) -> list[str]:
        """Declared category slugs this run could not resolve to a ``category`` row.

        Non-empty means withdrawal cannot be positively attributed this run, so
        :func:`sync_coverage` suppresses its prune entirely -- see the guard
        there for why an unattributable row must be kept rather than deleted.
        """

        return sorted(o.category_slug for o in self.outcomes if o.action == "unknown_category")

    @property
    def declared(self) -> int:
        """Rows this run left in place as declarations (created + updated + unchanged)."""

        return self.created + self.updated + self.unchanged

    @property
    def changed(self) -> bool:
        return self.created > 0 or self.updated > 0 or self.withdrawn > 0


class CoverageFloorError(ValueError):
    """The persisted coverage for a provider fell below the Q9-A evidence floor.

    The mirror image of :meth:`app.config.models.ProviderConfig.
    validate_coverage_floor`, which enforces the same rule at config load.
    Raised by :func:`sync_coverage` so a sync that would erode a provider below
    the floor aborts and is rolled back with the caller's transaction, rather
    than committing the erosion and reporting it afterwards.
    """


def _assert_persisted_coverage_floor(
    session: Session,
    provider: Provider,
    result: CoverageSyncResult,
    *,
    checked: bool,
) -> None:
    """Q9-A as a DATABASE invariant, not merely a config-load one.

    ``ProviderConfig.validate_coverage_floor()`` proves the *file* declares at
    least :data:`app.config.models.MIN_EVIDENCE_BACKED_COVERAGE` evidence-backed
    ``verified_free``/``offered_no_z0`` categories. That says nothing about what
    actually landed in the database: a resolution failure, a hand-edited row or
    a partially synced database could leave a provider below the floor while
    every surviving row remained individually legal. This re-reads the
    **persisted** rows after the flush and applies the same predicate.

    ``checked=False`` skips the assertion for the one documented case where it
    would be meaningless: a database with no canonical taxonomy at all (pre-0010),
    where :func:`sync_coverage` writes nothing and there is nothing to erode.
    That is the **only** skip. In particular zero persisted rows is *not* a skip
    but the maximal erosion: ``ProviderConfig.coverage`` is mandatory and must
    carry exactly the fourteen canonical slugs, so a legitimately zero-row
    provider cannot exist and zero rows always means total failure. An earlier
    revision returned early on ``not rows``, which made 20% erosion raise while
    100% erosion stayed silent -- the exact inversion of what a floor is for.
    """

    if not checked:
        return

    rows = list(
        session.execute(
            select(ProviderCategoryCoverage).where(
                ProviderCategoryCoverage.provider_id == provider.id
            )
        ).scalars()
    )

    slug_by_id = {row.id: row.slug for row in session.execute(select(Category)).scalars()}
    backed = sorted(
        slug_by_id.get(row.category_id, str(row.category_id))
        for row in rows
        if row.state in EVIDENCE_BACKED_COVERAGE_STATES
        and (row.source_id is not None or (row.evidence_url or "").strip())
    )
    if len(backed) >= MIN_EVIDENCE_BACKED_COVERAGE:
        return

    causes = []
    if result.unresolved_sources:
        causes.append(f"unresolved source references: {', '.join(result.unresolved_sources)}")
    if result.unresolved_categories:
        causes.append(f"unresolved category references: {', '.join(result.unresolved_categories)}")
    cause = f" Contributing this run -- {'; '.join(causes)}." if causes else ""
    raise CoverageFloorError(
        f"provider {provider.slug!r}: after sync only {len(backed)} of the "
        f"{len(rows)} persisted coverage rows are evidence-backed "
        f"{'/'.join(EVIDENCE_BACKED_COVERAGE_STATES)} "
        f"(found: {', '.join(backed) if backed else 'none'}); at least "
        f"{MIN_EVIDENCE_BACKED_COVERAGE} are required -- "
        f"{MIN_EVIDENCE_BACKED_COVERAGE - len(backed)} more needed.{cause} The "
        "config-load floor passed, so the database has drifted below what the "
        "provider file declares; the sync is aborted so the shortfall is never "
        "committed."
    )


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

    A **reference this sync cannot resolve is not a withdrawal**, on either axis.
    If a declared ``source`` id has no matching ``source`` row yet (a partially
    synced database), the pair is still registered as declared and any stored row
    is left exactly as it stands. If a declared *category* slug does not resolve
    -- taxonomy drift, typically a renamed slug, where the FK does **not** cascade
    because the category row still exists -- then withdrawal cannot be attributed
    at all, so the prune is suppressed wholesale for that run
    (``prune_suppressed``). Both conditions are reported as ``unknown_source`` /
    ``unknown_category`` outcomes rather than silently pruning an evidence-backed
    declaration.

    Finally the **persisted** rows are re-read and held to the same Q9-A
    evidence floor the config loader enforces
    (:func:`_assert_persisted_coverage_floor`); a shortfall raises
    :class:`CoverageFloorError` so the erosion is rolled back with the caller's
    transaction instead of being committed and noticed later.

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

    categories = list(session.execute(select(Category)).scalars())
    category_ids = {row.slug: row.id for row in categories}
    category_slugs = {row.id: row.slug for row in categories}
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
            # This declaration names a category slug the database does not have:
            # a pre-0010 database, or -- the case that matters -- taxonomy drift
            # such as a RENAMED slug. A rename is *not* covered by the FK's ON
            # DELETE CASCADE: the category row still exists under its new slug,
            # so the stored coverage row survives, keyed on a category_id this
            # sync can no longer name. That makes it indistinguishable from a
            # genuinely withdrawn pair, which is why the prune below is
            # suppressed entirely whenever this branch fires. Registering an id
            # here is impossible -- not having one is the definition of this
            # branch -- so attribution, not registration, is the fix.
            result.outcomes.append(
                CoverageDeclarationOutcome(slug, "unknown_category", entry.state)
            )
            continue

        # Register the pair as declared BEFORE any further resolution step that
        # is allowed to fail. A reference this sync cannot resolve is a
        # *resolution failure*, never a withdrawal of the declaration -- and the
        # prune loop below deletes exactly the rows this set does not contain.
        # Reaching that loop with a still-declared pair unregistered is what
        # silently deleted evidence-backed rows and eroded the Q9-A floor.
        declared_category_ids.add(category_id)

        source_id: int | None = None
        if entry.source is not None:
            source_id = source_ids.get(entry.source)
            if source_id is None:
                # The config validator already proved the id is declared in the
                # file, so this only happens against a partially synced database.
                # Leave any stored row exactly as it is rather than rewriting it
                # with a null source (which would strip its provenance) or
                # dropping it (which would withdraw a declaration the config
                # still makes). The outcome keeps the condition observable, and
                # carries the surviving row's id so a caller can see it stood.
                stored = existing_rows.get(category_id)
                result.outcomes.append(
                    CoverageDeclarationOutcome(
                        slug,
                        "unknown_source",
                        entry.state,
                        stored.id if stored is not None else None,
                    )
                )
                continue

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

    # Withdrawal must be positively proven. A row is pruned because the config
    # stopped declaring its pair -- which is only inferable when every
    # declaration resolved to a category id. If any did not, the row that
    # declaration refers to is keyed on an id this sync cannot name, so it is
    # indistinguishable from a genuinely withdrawn pair and pruning would delete
    # a still-declared row (the renamed-slug case: the FK does not cascade
    # because the category still exists). Erring toward keeping an unattributable
    # row matches "a resolution failure is not a withdrawal"; the outcomes keep
    # the condition observable and the floor check below still runs.
    if result.unresolved_categories:
        result.prune_suppressed = True
    else:
        for category_id, row in existing_rows.items():
            if category_id in declared_category_ids:
                continue
            coverage_id, state = row.id, row.state
            session.delete(row)
            result.outcomes.append(
                CoverageDeclarationOutcome(
                    category_slugs.get(category_id, ""), "withdrawn", state, coverage_id
                )
            )

    session.flush()
    _assert_persisted_coverage_floor(session, provider, result, checked=bool(category_ids))
    return result


def sync_provider(session: Session, config: ProviderConfig) -> SyncResult:
    """Upsert ``config`` into ``provider`` + ``source`` rows; return a summary.

    Idempotent on ``Provider.slug`` and ``Source.slug``: re-running against the
    same config produces zero changes. After the source sync it applies the
    declared ``service_categories`` mapping (:func:`categorise_services`) and the
    declared ``coverage`` block (:func:`sync_coverage`, which runs after the
    sources so its ``source`` references resolve); both are themselves
    idempotent. The caller owns the transaction (this flushes but never commits).

    **All four writes are one atomic unit -- for any failure in those four
    writes.** They run inside a SAVEPOINT which is
    rolled back -- and the original exception re-raised -- when any of them
    raises, so for any failure *in those four writes* the provider is either
    fully synced or entirely untouched, and a half-synced provider is never
    handed to the caller's transaction.

    That guarantee covers failures *in the four writes*. It does not extend to a
    failure raised while the SAVEPOINT is being **released** -- notably from an
    ``after_transaction_end`` event listener, which SQLAlchemy dispatches after
    the ``RELEASE SAVEPOINT`` has already succeeded. By then the four writes
    belong to the caller's enclosing transaction and this function, which does
    not own that transaction, can no longer revert them. The caller therefore
    receives the original exception (identity and note handling are unchanged in
    that path) but a subsequent ``commit()`` persists the provider anyway: a sync
    reported as failed can still be committed as complete. No module under
    ``apps/`` registered such a listener at the time of writing, verified by
    inspection, so this is a library seam rather than a live defect -- a
    point-in-time observation, not a standing guarantee;
    ``tests/integration/test_ingest_sync_savepoint.py`` pins the
    boundary, and asserts that importing ``apps/api/app`` registers no new
    *class-level* ``after_transaction_end`` listener on ``Session`` or a subclass
    -- a tripwire for the realistic regression, with two limits documented in
    that test rather than a proof that none exists anywhere. See the comment at the
    ``savepoint.commit()`` call for why no guard is applied here.

    This makes the guarantee local to this function instead of a property of who
    calls it. :func:`sync_coverage` protects the Q9-A evidence floor by *raising*
    (:class:`CoverageFloorError`), which only prevents the erosion because the
    flushed DELETEs die with the transaction. A caller shaped ``try:
    sync_provider(...) except Exception: continue`` followed by a ``commit()``
    -- the shape ``app.ingest.runner`` already uses per source, and the expected
    shape of a batch runner over several providers -- would otherwise commit
    exactly the erosion the raise was meant to prevent.

    The savepoint is scoped to the whole provider unit rather than to the
    coverage block alone, deliberately. On a *new* provider's first sync there is
    no prior coverage to revert to, so a coverage-only savepoint would commit a
    provider carrying **zero** coverage rows -- every category then reads
    ``unknown`` and :func:`_assert_persisted_coverage_floor` cannot detect it,
    which is worse than aborting. Provider-unit scope keeps the unit coherent
    against a failure in the four writes: fully synced, or untouched, never
    provider-without-coverage.

    The exception is re-raised, never converted into a return value. Rolling back
    and returning would hand the caller a ``SyncResult`` describing writes that
    no longer exist -- *reporting success* for a sync that did not happen, a
    silent degradation in place of a loud one. (The ``SyncResult`` itself holds
    primitive ids and dataclasses, so it would remain readable after the
    rollback; that is precisely what makes returning it dangerous rather than
    merely broken.) The original exception reaches the caller unchanged in type
    and identity whatever happens inside the failure path: a failing rollback is
    attached to it as a note rather than replacing it, and if attaching the note
    fails too that failure is discarded rather than allowed to displace it. So
    ``CoverageFloorError`` still reaches the caller as itself.
    """

    savepoint = session.begin_nested()
    try:
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
        # Releasing the SAVEPOINT is the last thing that can fail here, and a
        # failure *in* the release (an ``after_transaction_end`` listener raising
        # after ``RELEASE SAVEPOINT`` has already succeeded) is outside what a
        # nested transaction can undo: the writes are the enclosing
        # transaction's by then, and that transaction belongs to the caller. It
        # is deliberately left unguarded. The options were measured on
        # PostgreSQL, with the caller holding unrelated flushed work:
        #   (a) mark the enclosing transaction rollback-only, so the caller's
        #       commit raises instead of persisting. SQLAlchemy 2.0 exposes no
        #       supported route: ``rollback_only`` is a ``join_transaction_mode``
        #       value for externally-supplied connections, not a session flag,
        #       and ``PendingRollbackError`` is reachable only by writing the
        #       private ``SessionTransaction._state`` / ``_rollback_exception``.
        #       Faking an internal state-machine transition would break silently
        #       on a rename -- no test would go red until someone registered a
        #       listener -- so the boundary would quietly become false again.
        #       The public alternatives (``Session.rollback``/``invalidate``/
        #       ``close``) all leave the caller's commit succeeding *silently*
        #       and additionally discard the caller's own unrelated work, which
        #       trades this narrow boundary for unbounded loss in the caller's
        #       scope.
        #   (b) narrow the window: impossible, the dangerous step is the
        #       terminating one, so anything reordered after it inherits the
        #       problem.
        #   (d) own the transaction outright (or use a dedicated connection).
        #       This is the only option that solves rather than reports, and it
        #       is rejected on blast radius, not merit: it inverts the
        #       caller-owns-the-transaction invariant held since F005 slice 1 and
        #       breaks ``runner.py``'s per-source savepoints. Revisit it only if
        #       a listener is ever registered, which a test now prevents
        #       happening unnoticed.
        # So the boundary is documented and tested instead of guarded.
        savepoint.commit()
    except BaseException as exc:
        # Everything in this block exists to protect one thing: that ``exc`` --
        # the original failure -- is what the caller receives, unchanged in type
        # and identity. Only four statements here can raise, and they are a
        # closed set: the ``savepoint.is_active`` read, ``savepoint.rollback()``,
        # the ``exc.add_note(...)`` statement (whose f-string also invokes
        # ``rollback_exc.__repr__``, dispatched just as virtually), and the bare
        # ``raise``, which introduces nothing new. The first three are guarded
        # below; nothing else in this block dispatches user- or library-supplied
        # code, so there is no further masking path to find.
        #
        # The rollback is attempted unconditionally rather than gated on
        # ``savepoint.is_active``: that flag is also False for a nested
        # transaction the failure merely *deactivated* -- a flush IntegrityError
        # leaves exactly that state -- which still needs its SAVEPOINT released,
        # so gating on it would skip a rollback that is genuinely required. The
        # flag is read for diagnosis only, and inside the guard so that even a
        # raising ``is_active`` cannot displace the primary exception.
        was_active: object = "unread"
        try:
            was_active = savepoint.is_active
            savepoint.rollback()
        except BaseException as rollback_exc:
            try:
                exc.add_note(
                    "app.ingest.config_sync.sync_provider: rolling the provider "
                    "SAVEPOINT back failed and was suppressed so it could not "
                    f"displace this exception (savepoint.is_active={was_active}): "
                    f"{rollback_exc!r}"
                )
            except BaseException:
                # ``add_note`` is virtually dispatched, so a hostile or broken
                # exception type can make it raise too. Discarding that failure
                # is correct rather than lazy at this depth: the note *was* the
                # channel for reporting a suppressed failure, so there is no
                # remaining channel to report its own failure through, and any
                # alternative (re-raising, logging into the exception) would put
                # the primary exception back at risk. The primary exception is
                # the thing that must survive; it does.
                pass
        # Re-raise unconditionally, and only ever the original: a caller must
        # never receive a success-shaped result for a sync that was rolled back.
        raise
    return result


__all__: Sequence[str] = (
    "DEFAULT_PROVIDER_TYPE",
    "CategorisationResult",
    "CoverageDeclarationOutcome",
    "CoverageFloorError",
    "CoverageSyncResult",
    "ServiceCategorisation",
    "SourceSyncOutcome",
    "SyncResult",
    "categorise_services",
    "sync_coverage",
    "sync_provider",
)
