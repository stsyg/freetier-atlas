"""Live PostgreSQL contract for Vercel's zero-source coverage declaration."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.db import get_session
from app.ingest.fetch import OfflineFetcher
from app.ingest.runner import fetch_policy_for, run_provider_scans
from app.main import app
from app.models.domain import (
    Candidate,
    ChangeEvent,
    DiscoveryCandidate,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    ProviderCategoryCoverage,
    Quota,
    ReviewItem,
    ScanRun,
    Snapshot,
    Source,
)
from app.read_api.taxonomy import canonical_slugs
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "vercel.example.yaml"

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start PostgreSQL and export it to enable.",
)

GRAPH_MODELS = (
    ScanRun,
    Snapshot,
    Candidate,
    Evidence,
    Offer,
    OfferVersion,
    Quota,
    ChangeEvent,
    DiscoveryCandidate,
    ReviewItem,
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
    model = load_and_validate(CONFIG_PATH)
    assert isinstance(model, ProviderConfig)
    assert model.sources == []
    return model


def _count(session: Session, model: type[object]) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


@skip_without_db
def test_zero_source_sync_runner_and_read_api_contract(session: Session) -> None:
    config = _config()

    first = run_provider_scans(
        session,
        config,
        OfflineFetcher(fetch_policy_for(config)),
        publish=True,
    )
    assert first.configured_sources == 0
    assert first.scanned == 0
    assert first.failed == 0
    assert first.sources == []
    assert first.sync is not None
    assert first.sync.provider_action == "created"
    assert first.sync.created == first.sync.updated == first.sync.unchanged == 0
    assert first.sync.coverage is not None
    assert first.sync.coverage.created == 14

    provider = session.execute(select(Provider).where(Provider.slug == "vercel")).scalar_one()
    assert _count(session, Provider) >= 1
    assert (
        session.execute(
            select(func.count()).select_from(Source).where(Source.provider_id == provider.id)
        ).scalar_one()
        == 0
    )
    coverage = list(
        session.execute(
            select(ProviderCategoryCoverage).where(
                ProviderCategoryCoverage.provider_id == provider.id
            )
        ).scalars()
    )
    assert len(coverage) == 14
    assert all(row.source_id is None for row in coverage)
    assert all((row.evidence_url or "").startswith("https://vercel.com/") for row in coverage)
    assert all(_count(session, model) == 0 for model in GRAPH_MODELS)

    second = run_provider_scans(
        session,
        config,
        OfflineFetcher(fetch_policy_for(config)),
        publish=True,
    )
    assert second.sync is not None
    assert second.sync.provider_action == "unchanged"
    assert second.sync.coverage is not None
    assert second.sync.coverage.unchanged == 14
    assert second.sync.changed is False
    assert second.sources == []
    assert all(_count(session, model) == 0 for model in GRAPH_MODELS)

    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        matrix_response = client.get("/catalogue/categories")
        offers_response = client.get("/catalogue/providers/vercel/offers")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert matrix_response.status_code == 200
    cells = {
        row["slug"]: next(cell for cell in row["providers"] if cell["provider_slug"] == "vercel")
        for row in matrix_response.json()["categories"]
    }
    assert tuple(cells) == canonical_slugs()
    for slug, declaration in config.coverage.items():
        cell = cells[slug]
        assert cell["state"] == declaration.state
        assert cell["declared_state"] == declaration.state
        assert cell["derived_state"] == "unknown"
        assert cell["mismatch"] is False
        assert cell["rationale"] == declaration.rationale
        assert cell["evidence_url"] == declaration.evidence_url
        assert cell["published_offer_count"] == 0
        assert cell["free_offer_count"] == 0
    assert offers_response.status_code == 200
    assert offers_response.json() == []

    stale = Source(
        provider_id=provider.id,
        slug="vercel-retired-source",
        adapter_type="html",
        trust_level="official",
        official=True,
        endpoint="https://vercel.com/docs/retired",
        schedule="official_pages",
        parser_profile="retired_profile",
        enabled=True,
    )
    session.add(stale)
    session.flush()
    before = {model: _count(session, model) for model in GRAPH_MODELS}

    retained = run_provider_scans(
        session,
        config,
        OfflineFetcher(fetch_policy_for(config)),
        publish=True,
    )
    assert retained.sources == []
    assert retained.failed == 0
    assert session.get(Source, stale.id) is stale
    assert {model: _count(session, model) for model in GRAPH_MODELS} == before
