"""Integration tests for idempotent config->DB sync (F005 slice 1).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack smoke
scripts and CI drive this against the live compose Postgres). Proves that
:func:`app.ingest.config_sync.sync_provider` turns the real Cloudflare provider
configuration into ``provider`` + ``source`` rows, bridging the YAML/DB field
name gaps, and that a second run against the same config is a genuine no-op: it
creates no duplicate rows and reports zero changes (idempotent on the stable
``Provider.slug`` / ``Source.slug`` keys added by migration 0007).

Every test runs inside a transaction that is rolled back, leaving data clean.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.loader import load_and_validate
from app.config.models import (
    MIN_EVIDENCE_BACKED_COVERAGE,
    CoverageDeclaration,
    ProviderConfig,
)
from app.ingest.config_sync import (
    CoverageFloorError,
    categorise_services,
    sync_coverage,
    sync_provider,
)
from app.models.domain import (
    Category,
    OfferVersion,
    Provider,
    ProviderCategoryCoverage,
    Service,
    Source,
)
from app.models.vocab import COVERAGE_STATES, EVIDENCE_BACKED_COVERAGE_STATES
from app.read_api import queries
from app.read_api import service as read_service
from app.read_api.taxonomy import canonical_slugs
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.upgrade(_alembic_config(), "head")
    eng = create_engine(DATABASE_URL)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()


def _config() -> ProviderConfig:
    model = load_and_validate(str(CONFIG_PATH))
    assert isinstance(model, ProviderConfig)
    return model


@skip_without_db
def test_sync_creates_provider_and_sources_with_bridged_fields(session: Session) -> None:
    config = _config()

    result = sync_provider(session, config)

    assert result.provider_action == "created"
    assert result.created == len(config.sources)
    assert result.updated == 0

    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()
    assert provider.name == "Cloudflare"
    assert set(provider.official_domains) == {"cloudflare.com", "developers.cloudflare.com"}

    sources = {
        s.slug: s
        for s in session.execute(select(Source).where(Source.provider_id == provider.id)).scalars()
    }
    assert set(sources) == {s.id for s in config.sources}

    # Field bridging on the official Workers HTML source.
    workers = sources["cloudflare-workers-limits"]
    assert workers.adapter_type == "html"  # type -> adapter_type
    assert workers.endpoint == "https://developers.cloudflare.com/workers/platform/limits/"
    assert workers.parser_profile == "cloudflare_workers_limits"  # extraction_profile
    assert workers.schedule == "official_pages"  # schedule_ref -> schedule
    assert workers.trust_level == "official"
    assert workers.official is True
    assert workers.enabled is True

    # An MCP source carries no url/profile: the sync must not invent them.
    mcp = sources["cloudflare-docs-mcp"]
    assert mcp.adapter_type == "mcp"
    assert mcp.endpoint is None
    assert mcp.parser_profile is None


@skip_without_db
def test_sync_is_idempotent_no_duplicate_rows(session: Session) -> None:
    config = _config()

    first = sync_provider(session, config)
    assert first.changed is True

    providers_after_first = session.execute(
        select(func.count()).select_from(Provider).where(Provider.slug == "cloudflare")
    ).scalar_one()
    sources_after_first = session.execute(select(func.count()).select_from(Source)).scalar_one()

    # Second run against the byte-identical config changes nothing.
    second = sync_provider(session, config)

    assert second.provider_action == "unchanged"
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == len(config.sources)
    assert second.changed is False

    providers_after_second = session.execute(
        select(func.count()).select_from(Provider).where(Provider.slug == "cloudflare")
    ).scalar_one()
    sources_after_second = session.execute(select(func.count()).select_from(Source)).scalar_one()

    assert providers_after_second == providers_after_first == 1
    assert sources_after_second == sources_after_first == len(config.sources)


@skip_without_db
def test_sync_detects_and_applies_a_real_change(session: Session) -> None:
    config = _config()
    sync_provider(session, config)

    # Mutate the in-memory config (rename the provider) and re-sync: exactly the
    # provider row updates, still no new/duplicate rows.
    config.provider.name = "Cloudflare, Inc."
    result = sync_provider(session, config)

    assert result.provider_action == "updated"
    assert result.created == 0
    assert result.unchanged == len(config.sources)

    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()
    assert provider.name == "Cloudflare, Inc."


# --- categorise_services (F008 slice S1) ------------------------------------


@skip_without_db
def test_categorise_services_backfills_and_is_idempotent(session: Session) -> None:
    """A pre-existing uncategorised service is back-filled, then left alone."""

    config = _config()
    assert config.service_categories, "the Cloudflare example must declare service_categories"
    declared_name, declared_slug = sorted(config.service_categories.items())[0]

    # First sync creates the provider; no services exist yet.
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()

    # Seed the service UNCATEGORISED, exactly as the pre-F008 catalogue had it.
    service = Service(
        provider_id=provider.id,
        category_id=None,
        canonical_name=declared_name,
        deployment_model="managed",
    )
    session.add(service)
    session.flush()
    assert service.category_id is None

    result = categorise_services(session, config)
    assert result.updated == 1
    assert result.changed is True

    session.refresh(service)
    assert service.category_id is not None
    category = session.get(Category, service.category_id)
    assert category.slug == declared_slug

    # Second run reports ZERO changes and mutates nothing.
    again = categorise_services(session, config)
    assert again.updated == 0
    assert again.unchanged == 1
    assert again.changed is False
    session.refresh(service)
    assert service.category_id == category.id


@skip_without_db
def test_categorise_services_reports_undeclared_service_names(session: Session) -> None:
    """A declared name with no matching service row is a no-op, not an error."""

    config = _config()
    sync_provider(session, config)

    result = categorise_services(session, config)
    # No service rows exist at all, so every declared mapping is an unknown service.
    assert result.updated == 0
    assert result.unknown_services == len(config.service_categories)
    assert result.changed is False


@skip_without_db
def test_sync_provider_runs_categorisation(session: Session) -> None:
    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()

    declared_name, declared_slug = sorted(config.service_categories.items())[0]
    session.add(
        Service(
            provider_id=provider.id,
            category_id=None,
            canonical_name=declared_name,
            deployment_model="managed",
        )
    )
    session.flush()

    result = sync_provider(session, config)
    assert result.categorised == 1

    service = session.execute(
        select(Service).where(Service.canonical_name == declared_name)
    ).scalar_one()
    assert session.get(Category, service.category_id).slug == declared_slug


@skip_without_db
def test_categorise_services_never_writes_offer_rows(session: Session) -> None:
    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()
    declared_name = sorted(config.service_categories)[0]
    session.add(
        Service(
            provider_id=provider.id,
            category_id=None,
            canonical_name=declared_name,
            deployment_model="managed",
        )
    )
    session.flush()

    before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    categorise_services(session, config)
    after = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    assert after == before


@skip_without_db
def test_removing_a_service_category_declaration_reverts_it_to_null(session: Session) -> None:
    """Declared metadata must be WITHDRAWABLE, not merely overwritable.

    The S1 evaluator found that deleting a ``service_categories`` entry left the
    service pinned to its old category forever -- a silent stale claim. Removing
    the declaration must converge the row back to uncategorised.
    """

    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()

    declared_name, declared_slug = sorted(config.service_categories.items())[0]
    service = Service(
        provider_id=provider.id,
        category_id=None,
        canonical_name=declared_name,
        deployment_model="managed",
    )
    session.add(service)
    session.flush()

    assert categorise_services(session, config).updated == 1
    session.refresh(service)
    assert session.get(Category, service.category_id).slug == declared_slug

    # Withdraw the declaration from the config and re-sync.
    config.service_categories = {
        name: slug for name, slug in config.service_categories.items() if name != declared_name
    }
    result = categorise_services(session, config)

    assert result.withdrawn == 1
    assert result.changed is True
    session.refresh(service)
    assert service.category_id is None, "a withdrawn declaration must revert to uncategorised"

    # ...and the withdrawal is itself idempotent.
    again = categorise_services(session, config)
    assert again.withdrawn == 0
    assert again.changed is False


# --- sync_coverage (F008 slice S2) ------------------------------------------


def _coverage_rows(session: Session, provider_id: int) -> dict[str, ProviderCategoryCoverage]:
    rows = session.execute(
        select(ProviderCategoryCoverage, Category.slug)
        .join(Category, Category.id == ProviderCategoryCoverage.category_id)
        .where(ProviderCategoryCoverage.provider_id == provider_id)
    ).all()
    return {slug: row for row, slug in rows}


@skip_without_db
def test_sync_coverage_writes_exactly_fourteen_legal_declarations(session: Session) -> None:
    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()

    total = session.execute(
        select(func.count())
        .select_from(ProviderCategoryCoverage)
        .where(ProviderCategoryCoverage.provider_id == provider.id)
    ).scalar_one()
    assert total == 14, "every canonical category must carry an explicit declaration"

    rows = _coverage_rows(session, provider.id)
    assert set(rows) == set(canonical_slugs())
    for slug, row in rows.items():
        assert row.state is not None, slug
        assert row.state in COVERAGE_STATES, (slug, row.state)
        assert row.declared_at is not None, slug
        if row.state == "not_offered":
            assert (row.rationale or "").strip(), slug
        if row.state in {"verified_free", "offered_no_z0"}:
            assert row.source_id is not None or (row.evidence_url or "").strip(), slug


@skip_without_db
def test_sync_coverage_resolves_declared_source_references(session: Session) -> None:
    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()
    rows = _coverage_rows(session, provider.id)

    referenced = {slug: d.source for slug, d in config.coverage.items() if d.source}
    assert referenced, "the shipped Cloudflare coverage block must cite at least one source"
    for slug, source_slug in referenced.items():
        row = rows[slug]
        assert row.source_id is not None, slug
        assert session.get(Source, row.source_id).slug == source_slug


@skip_without_db
def test_sync_coverage_is_idempotent(session: Session) -> None:
    config = _config()
    first = sync_provider(session, config)
    assert first.coverage.created == 14
    assert first.coverage.changed is True

    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()
    before = {
        slug: (r.state, r.declared_at) for slug, r in _coverage_rows(session, provider.id).items()
    }

    second = sync_provider(session, config)

    assert second.coverage.created == 0
    assert second.coverage.updated == 0
    assert second.coverage.unchanged == 14
    assert second.coverage.changed is False
    assert second.changed is False

    total = session.execute(
        select(func.count())
        .select_from(ProviderCategoryCoverage)
        .where(ProviderCategoryCoverage.provider_id == provider.id)
    ).scalar_one()
    assert total == 14, "a re-run must not duplicate declarations"

    after = {
        slug: (r.state, r.declared_at) for slug, r in _coverage_rows(session, provider.id).items()
    }
    assert after == before, "an unchanged declaration must not be re-stamped"


@skip_without_db
def test_a_changed_declaration_overwrites_the_stored_row(session: Session) -> None:
    """WITHDRAWABLE DECLARATIONS: the DB converges to the new declared truth."""

    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()

    target = next(slug for slug, d in config.coverage.items() if d.state == "verified_free")
    stored = _coverage_rows(session, provider.id)[target]
    assert stored.state == "verified_free"
    assert stored.source_id is not None or stored.evidence_url

    # The maintainers retract the free claim: verified_free -> unknown. A real
    # config cannot drop below the Q9-A floor while doing so (the loader would
    # reject it, and sync_coverage now enforces the same floor against the
    # persisted rows), so the retraction is paired with a newly evidenced
    # category exactly as a maintainer would have to write it.
    replacement = next(slug for slug, d in config.coverage.items() if d.state == "unknown")
    config.coverage[target] = CoverageDeclaration(state="unknown")
    config.coverage[replacement] = CoverageDeclaration(
        state="verified_free",
        evidence_url="https://developers.cloudflare.com/r2/pricing/",
    )
    result = sync_coverage(session, config)

    assert result.updated == 2
    assert result.changed is True

    reloaded = _coverage_rows(session, provider.id)[target]
    assert reloaded.state == "unknown", "a retracted claim must NOT keep its old state"
    # The provenance of the retracted claim is dropped with it.
    assert reloaded.source_id is None
    assert reloaded.evidence_url is None
    # Still exactly one row for the pair -- an overwrite, not an append.
    assert len(_coverage_rows(session, provider.id)) == 14


@skip_without_db
def test_a_row_the_config_no_longer_declares_is_withdrawn(session: Session) -> None:
    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()
    assert len(_coverage_rows(session, provider.id)) == 14

    # Simulate a row written by an older schema that the current config drops.
    dropped = sorted(config.coverage)[0]
    config.coverage = {k: v for k, v in config.coverage.items() if k != dropped}

    result = sync_coverage(session, config)

    assert result.withdrawn == 1
    assert result.changed is True
    rows = _coverage_rows(session, provider.id)
    assert dropped not in rows, "an undeclared row must not linger as an invisible claim"
    assert len(rows) == 13


@skip_without_db
def test_an_unresolvable_source_reference_does_not_withdraw_the_declaration(
    session: Session,
) -> None:
    """OBSERVATION A: a reference this sync cannot resolve is NOT a withdrawal.

    The Level-2 evaluator's live reproduction. ``sync_coverage`` used to
    ``continue`` past an unresolvable ``source`` **without** registering the pair
    as declared, so the prune loop treated it as no-longer-declared and DELETED
    the row. Renaming the two referenced source slugs took Cloudflare from 14
    rows to 12 and dropped its evidence-backed count from 3 (exactly the Q9-A
    floor) to 1, with no error raised anywhere.
    """

    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()

    before = _coverage_rows(session, provider.id)
    assert len(before) == 14

    affected = sorted(slug for slug, entry in config.coverage.items() if entry.source)
    assert len(affected) >= 2, "the reproduction needs at least two source-backed declarations"
    referenced = {config.coverage[slug].source for slug in affected}
    expected = {slug: (before[slug].state, before[slug].source_id) for slug in affected}
    assert all(source_id is not None for _, source_id in expected.values())

    # The reproduction: rename the referenced source slugs directly in the
    # database so the declared references no longer resolve.
    renamed = 0
    for source in session.execute(
        select(Source).where(Source.provider_id == provider.id)
    ).scalars():
        if source.slug in referenced:
            source.slug = f"renamed-{source.slug}"
            renamed += 1
    session.flush()
    assert renamed == len(referenced)

    result = sync_coverage(session, config)

    # The condition stays observable...
    assert result.unknown_sources == len(affected)
    assert result.unresolved_sources == affected
    # ...but it is emphatically not a withdrawal.
    assert result.withdrawn == 0, "an unresolvable reference must never prune a declared row"

    after = _coverage_rows(session, provider.id)
    assert len(after) == 14, "a still-declared pair must survive a failed reference resolution"
    for slug in affected:
        assert slug in after
        assert (after[slug].state, after[slug].source_id) == expected[slug], (
            f"{slug} must keep its declared state and provenance untouched"
        )

    # And the public matrix still reports them as declared rather than regressing
    # to 'unknown' -- the user-visible symptom the evaluator observed.
    listed = queries.fetch_providers(session)
    matrix = read_service.serialize_category_matrix(
        listed,
        queries.category_map_for_providers(session, listed),
        queries.coverage_signal_context(session, listed),
    )
    cells = {
        (row.slug, cell.provider_slug): cell for row in matrix.categories for cell in row.providers
    }
    for slug in affected:
        cell = cells[(slug, "cloudflare")]
        assert cell.declared_state == expected[slug][0], slug
        assert cell.declared_state != "unknown", slug


def _backed_ids(rows: dict[str, ProviderCategoryCoverage]) -> set[int]:
    """Coverage row ids that count toward the Q9-A floor.

    Keyed on the row id rather than the category slug so the comparison survives
    a slug rename -- which is precisely the drift under test.
    """

    return {
        row.id
        for row in rows.values()
        if row.state in EVIDENCE_BACKED_COVERAGE_STATES
        and (row.source_id is not None or (row.evidence_url or "").strip())
    }


@skip_without_db
def test_an_unresolvable_category_reference_does_not_withdraw_the_declaration(
    session: Session,
) -> None:
    """F-1: the SAME rule on the category axis, which the first fix missed.

    The original comment justified skipping this branch on the grounds that
    ``category_id`` is a FK with ``ON DELETE CASCADE``, so no stored row could
    exist. That premise holds for a *deleted* category and fails for a
    **renamed** one: the category row still exists under its new slug, nothing
    cascades, and the coverage row survives keyed on an id this sync can no
    longer name -- so the prune deleted it. The evaluator renamed one
    evidence-backed slug and got 14 -> 13 rows, backed 3 -> 2.

    Registering an id here is impossible (not having one *is* this branch), so
    the fix is attribution: withdrawal must be positively proven, and an
    unresolved category reference makes that impossible for the whole run.
    """

    config = _config()
    sync_provider(session, config)
    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()

    before = _coverage_rows(session, provider.id)
    assert len(before) == 14
    backed_before = _backed_ids(before)
    assert len(backed_before) == MIN_EVIDENCE_BACKED_COVERAGE

    # Rename one EVIDENCE-BACKED category slug directly in the database. The
    # category row survives, so the FK does not cascade -- this is drift, not
    # deletion, and it is exactly what the old comment assumed away.
    target = sorted(
        slug
        for slug, row in before.items()
        if row.state in EVIDENCE_BACKED_COVERAGE_STATES
        and (row.source_id is not None or (row.evidence_url or "").strip())
    )[0]
    category = session.execute(select(Category).where(Category.slug == target)).scalar_one()
    category.slug = f"renamed-{target}"
    session.flush()

    result = sync_coverage(session, config)

    # The condition stays observable...
    assert result.unknown_categories == 1
    assert result.unresolved_categories == [target]
    # ...and the prune is suppressed rather than deleting what it cannot attribute.
    assert result.prune_suppressed is True
    assert result.withdrawn == 0, "an unresolvable category reference must never prune a row"

    after = _coverage_rows(session, provider.id)
    assert len(after) == 14, "a still-declared pair must survive a failed category resolution"
    assert _backed_ids(after) == backed_before, "the Q9-A floor must not be eroded by drift"


@skip_without_db
def test_the_floor_catches_total_erosion_and_not_only_partial_erosion(
    session: Session,
) -> None:
    """F-2: zero persisted rows is the MAXIMAL erosion, never a reason to skip.

    An earlier revision returned early on ``not rows``, so 20% erosion raised
    while 100% erosion stayed silent -- the exact inversion of what a floor is
    for. ``ProviderConfig.coverage`` is mandatory and must carry exactly the
    fourteen canonical slugs, so a legitimately zero-row provider cannot exist:
    zero rows always means total failure.

    Reached here by onboarding a provider against a fully-migrated database
    whose taxonomy has drifted wholesale, so no declaration resolves and no row
    is ever written. Nothing pre-exists to preserve, so unlike the partial case
    there is no surviving row to fall back on.
    """

    config = _config()
    for category in session.execute(select(Category)).scalars():
        category.slug = f"drifted-{category.slug}"
    session.flush()
    # The taxonomy is present -- this is drift on a migrated DB, not a pre-0010
    # database -- so the floor check is emphatically in scope.
    assert session.execute(select(func.count()).select_from(Category)).scalar_one() == 14

    savepoint = session.begin_nested()
    with pytest.raises(CoverageFloorError) as excinfo:
        sync_provider(session, config)

    message = str(excinfo.value)
    assert "cloudflare" in message
    assert "0 of the 0 persisted coverage rows" in message, (
        "total erosion must be reported as such, not swallowed by an early return"
    )
    assert "unresolved category references" in message

    savepoint.rollback()
    remaining = session.execute(
        select(func.count())
        .select_from(ProviderCategoryCoverage)
        .join(Provider, Provider.id == ProviderCategoryCoverage.provider_id)
        .where(Provider.slug == "cloudflare")
    ).scalar_one()
    assert remaining == 0


@skip_without_db
def test_sync_coverage_aborts_when_the_persisted_rows_fall_below_the_q9a_floor(
    session: Session,
) -> None:
    """Q9-A is a DATABASE invariant, not only a config-load one.

    ``validate_coverage_floor()`` proves the *file* declares at least three
    evidence-backed categories. That says nothing about what actually lands in
    the database. Onboarding a provider against a partially synced database (its
    ``source`` rows not written yet) persists the declarations whose provenance
    is an ``evidence_url`` but silently skips the ones that cite a ``source`` --
    leaving the provider below the floor with every surviving row still
    individually legal. That must abort, not commit quietly.
    """

    config = _config()
    # A partially synced database: the provider is being written, its sources
    # are not there yet, so every source-backed declaration is unresolvable.
    config.sources = []

    savepoint = session.begin_nested()
    with pytest.raises(CoverageFloorError) as excinfo:
        sync_provider(session, config)

    message = str(excinfo.value)
    assert "cloudflare" in message
    assert str(MIN_EVIDENCE_BACKED_COVERAGE) in message
    for slug in sorted(slug for slug, entry in config.coverage.items() if entry.source):
        assert slug in message, "the message must name the unresolved references"

    # The erosion is rolled back with the caller's transaction: nothing commits.
    savepoint.rollback()
    remaining = session.execute(
        select(func.count())
        .select_from(ProviderCategoryCoverage)
        .join(Provider, Provider.id == ProviderCategoryCoverage.provider_id)
        .where(Provider.slug == "cloudflare")
    ).scalar_one()
    assert remaining == 0


@skip_without_db
def test_the_persisted_floor_check_is_silent_on_the_healthy_path(session: Session) -> None:
    """The new invariant must not fire on a normal sync or an idempotent re-run."""

    config = _config()
    first = sync_provider(session, config)
    assert first.coverage.created == 14

    provider = session.execute(select(Provider).where(Provider.slug == "cloudflare")).scalar_one()
    rows = _coverage_rows(session, provider.id)
    backed = [
        slug
        for slug, row in rows.items()
        if row.state in EVIDENCE_BACKED_COVERAGE_STATES
        and (row.source_id is not None or (row.evidence_url or "").strip())
    ]
    assert len(backed) >= MIN_EVIDENCE_BACKED_COVERAGE

    second = sync_provider(session, config)
    assert second.coverage.unchanged == 14
    assert second.coverage.changed is False


@skip_without_db
def test_sync_coverage_never_stores_a_derived_state(session: Session) -> None:
    """Q11: the table is the declaration; nothing derived is written to it."""

    config = _config()
    sync_provider(session, config)

    columns = {c.name for c in ProviderCategoryCoverage.__table__.columns}
    assert "derived_state" not in columns
    assert "derived_at" not in columns
