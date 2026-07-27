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
from app.config.models import ProviderConfig
from app.ingest.config_sync import categorise_services, sync_provider
from app.models.domain import Category, OfferVersion, Provider, Service, Source
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
