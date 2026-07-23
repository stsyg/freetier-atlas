"""Integration tests for the provider scan runner (F005 slice 1).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack smoke
scripts and CI drive this against the live compose Postgres). Proves the runtime
entrypoint :func:`app.ingest.runner.run_provider_scans` composes config sync +
scan + reconcile against the *real* Cloudflare configuration, driving the
captured official fixtures through the existing pipeline to produce
pre-publication ``candidate`` + official ``evidence`` rows -- and only those:

* the two official HTML limit pages (Workers, Pages) each yield exactly one
  candidate carrying the real captured free-tier facts, plus one official
  ``evidence`` row whose ``offer_version_id`` is NULL;
* a source whose adapter cannot even be built (the MCP source has no parser
  profile) is isolated as a per-source error and never aborts the whole run;
* NO ``offer`` / ``offer_version`` / ``quota`` row is created (there is no
  publication path in this slice).

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
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.models.domain import (
    Candidate,
    Evidence,
    Offer,
    OfferVersion,
    Quota,
    Source,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ingest" / "cloudflare" / "html"

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


def _run(session: Session):
    config = _config()
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)
    return config, run_provider_scans(session, config, fetcher)


@skip_without_db
def test_runner_extracts_official_candidates_and_evidence(session: Session) -> None:
    config, result = _run(session)

    # Config sync ran first and created every configured source.
    assert result.sync is not None
    assert result.sync.created == len(config.sources)

    outcomes = {o.slug: o for o in result.sources}

    # The two official HTML limit pages each extract exactly one candidate.
    for slug in ("cloudflare-workers-limits", "cloudflare-pages-limits"):
        outcome = outcomes[slug]
        assert outcome.status == "scanned", outcome.error
        assert outcome.documents == 1
        assert outcome.candidates == 1
        assert outcome.reconcile_added == 1  # first observation of each candidate

    # The MCP source cannot build an adapter (no parser profile) -> isolated
    # as a per-source error, not a whole-run abort.
    assert outcomes["cloudflare-docs-mcp"].status == "error"
    assert "Mcp" in outcomes["cloudflare-docs-mcp"].error or "profile" in (
        outcomes["cloudflare-docs-mcp"].error or ""
    )

    # Candidates carry the REAL captured Cloudflare free-tier facts.
    provider_sources = {
        s.slug: s
        for s in session.execute(
            select(Source).where(
                Source.slug.in_(["cloudflare-workers-limits", "cloudflare-pages-limits"])
            )
        ).scalars()
    }
    workers_source = provider_sources["cloudflare-workers-limits"]
    workers_candidate = session.execute(
        select(Candidate).where(Candidate.source_id == workers_source.id)
    ).scalar_one()
    assert workers_candidate.official is True
    assert workers_candidate.candidate_facts["service"] == "Cloudflare Workers"
    assert workers_candidate.candidate_facts["offer_type"] == "always_free"
    assert workers_candidate.candidate_facts["requests_per_day"] == "100,000/day"

    # Official evidence exists for each, and NEVER references an offer version.
    for source in provider_sources.values():
        evidence = list(
            session.execute(select(Evidence).where(Evidence.source_id == source.id)).scalars()
        )
        assert len(evidence) == 1
        assert evidence[0].official is True
        assert evidence[0].offer_version_id is None


@skip_without_db
def test_runner_never_writes_offer_offer_version_or_quota(session: Session) -> None:
    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    quotas_before = session.execute(select(func.count()).select_from(Quota)).scalar_one()

    _run(session)

    assert session.execute(select(func.count()).select_from(Offer)).scalar_one() == offers_before
    assert (
        session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
        == versions_before
    )
    assert session.execute(select(func.count()).select_from(Quota)).scalar_one() == quotas_before


@skip_without_db
def test_runner_extraction_is_reproducible(session: Session) -> None:
    config = _config()
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)

    first = run_provider_scans(session, config, fetcher)
    # A second run over the same session/config: sync is a no-op and the
    # re-scan detects zero changes for the already-seen candidates.
    second = run_provider_scans(session, config, fetcher)

    assert first.sync is not None and first.sync.created == len(config.sources)
    assert second.sync is not None and second.sync.changed is False

    second_outcomes = {o.slug: o for o in second.sources}
    for slug in ("cloudflare-workers-limits", "cloudflare-pages-limits"):
        assert second_outcomes[slug].status == "scanned"
        assert second_outcomes[slug].candidates == 1
        assert second_outcomes[slug].reconcile_added == 0  # unchanged on re-scan
        assert second_outcomes[slug].reconcile_modified == 0
