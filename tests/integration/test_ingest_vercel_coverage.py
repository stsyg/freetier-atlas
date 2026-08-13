"""Persisted fourteen-category Vercel coverage matrix contract."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config import load_and_validate
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.main import app
from app.models.domain import Provider, ProviderCategoryCoverage
from app.read_api.taxonomy import canonical_slugs
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "vercel.example.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ingest" / "vercel" / "html"

skip_without_db = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


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


@skip_without_db
def test_declared_coverage_remains_visible_with_zero_published_offers(
    session: Session,
) -> None:
    config = load_and_validate(CONFIG_PATH)
    result = run_provider_scans(
        session,
        config,
        build_fixture_fetcher(config, FIXTURES),
        reconcile=True,
        publish=True,
    )
    assert result.total_published == 0

    provider = session.scalar(select(Provider).where(Provider.slug == "vercel"))
    coverage = list(
        session.scalars(
            select(ProviderCategoryCoverage).where(
                ProviderCategoryCoverage.provider_id == provider.id
            )
        )
    )
    assert len(coverage) == 14

    from app.db import get_session

    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        matrix = client.get("/catalogue/categories")
        offers = client.get("/catalogue/providers/vercel/offers")
    finally:
        app.dependency_overrides.pop(get_session, None)

    cells = {
        row["slug"]: next(cell for cell in row["providers"] if cell["provider_slug"] == "vercel")
        for row in matrix.json()["categories"]
    }
    assert tuple(cells) == canonical_slugs()
    assert all(cell["published_offer_count"] == 0 for cell in cells.values())
    assert offers.json() == []
