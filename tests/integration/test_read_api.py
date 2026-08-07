"""Integration tests for the read-only catalogue API (F005 slice 3).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack
smoke scripts and CI drive this against the live compose Postgres). These prove
the read API's real query + serialization path end-to-end against the *actual*
schema (migrations 0001..0007): we run the S1 scan + S2 gated publication so the
database holds a genuinely published Cloudflare catalogue, then read it back
through :mod:`app.read_api.queries` + :mod:`app.read_api.service` and assert each
slice-3 scope item reflects the real published data.

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
from app.models.domain import Candidate
from app.read_api import queries, service
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


def _publish(session: Session) -> None:
    config = _config()
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)
    result = run_provider_scans(session, config, fetcher, publish=True)
    session.flush()
    # A fresh DB publishes new versions; a DB that already holds this catalogue
    # (e.g. a shared/live Postgres seeded by an earlier run) reports the identical
    # facts as idempotent "unchanged". Either way the read path must see a
    # genuinely published Cloudflare offer, so assert on the resulting catalogue
    # state rather than on this run's freshly-published counter.
    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"
    published_offers = [
        offer for svc in provider.services for offer in svc.offers if queries.is_published(offer)
    ]
    settled = sum((o.published or 0) + (o.publish_unchanged or 0) for o in result.sources)
    assert published_offers, (
        "expected at least one published Cloudflare offer "
        f"(this run: published+unchanged={settled})"
    )


def _cloudflare(session: Session):
    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"
    return provider


# --- scope 1: providers list + detail --------------------------------------


@skip_without_db
def test_providers_reflect_published_catalogue(session: Session) -> None:
    _publish(session)

    providers = queries.fetch_providers(session)
    slugs = {p.slug for p in providers}
    assert "cloudflare" in slugs

    provider = _cloudflare(session)
    summary = service.serialize_provider_summary(provider)
    assert summary.name
    assert summary.published_offer_count >= 1
    # completeness/freshness derived from the published versions' signals.
    assert summary.completeness is not None
    assert summary.freshness is not None

    detail = service.serialize_provider_detail(provider)
    assert detail.official_domains  # real metadata, not fabricated


# --- scope 2: category / service states -------------------------------------


@skip_without_db
def test_category_states_reflect_published_offers(session: Session) -> None:
    _publish(session)
    provider = _cloudflare(session)
    cat_ids = [s.category_id for s in provider.services if s.category_id is not None]
    cat_map = queries.category_map(session, cat_ids)

    states = service.serialize_category_states(provider, cat_map)
    assert states.provider_slug == "cloudflare"
    all_offers = [
        offer for group in states.categories for svc in group.services for offer in svc.offers
    ]
    assert all_offers
    assert all(o.zero_cost_class for o in all_offers)


# --- scope 3: offers + Z0 reasons + quota -----------------------------------


@skip_without_db
def test_offer_detail_has_z0_reasons_quota_and_confidence_label(session: Session) -> None:
    _publish(session)
    provider = _cloudflare(session)
    published = [o for s in provider.services for o in s.offers if queries.is_published(o)]
    assert published
    offer = published[0]
    cat_map = queries.category_map(session, [offer.service.category_id])

    detail = service.serialize_offer_detail(offer, cat_map)
    assert detail.zero_cost_class == "Z0_TRUE_FREE"
    assert detail.reasons, "Z0 reasons must be surfaced from material_facts"
    assert detail.quotas, "quota rows must be surfaced"
    # High-confidence Cloudflare publish -> plain-language label is the primary
    # field; the numeric score lives only in the advanced block.
    assert detail.confidence_label == "high"
    dumped = detail.model_dump()
    assert "confidence" not in dumped
    assert dumped["advanced"]["score"] >= 0.90


# --- scope 4: evidence + confidence label -----------------------------------


@skip_without_db
def test_offer_evidence_is_official_and_linked_to_version(session: Session) -> None:
    _publish(session)
    provider = _cloudflare(session)
    offer = next(o for s in provider.services for o in s.offers if queries.is_published(o))
    version = queries.latest_version(offer)
    assert version is not None

    rows = queries.fetch_offer_evidence(session, offer_version_id=version.id)
    assert rows, "published offer must have official evidence"
    response = service.serialize_offer_evidence(offer, rows)
    assert response.confidence_label == "high"
    for ev in response.evidence:
        assert ev.official is True
        assert ev.offer_version_id == version.id
        assert ev.source is not None
        assert ev.snapshot is not None


@skip_without_db
def test_evidence_query_excludes_candidate_only_rows(session: Session) -> None:
    """Guard: only offer_version-linked evidence is returned; candidate rows aren't.

    The scan stage creates ``candidate`` rows and their pre-publication evidence.
    The read query filters strictly on ``offer_version_id``, so:

    * every row it returns is linked to the published version (never candidate-only), and
    * a version id with no evidence returns an empty list (no accidental leakage).
    """

    _publish(session)
    provider = _cloudflare(session)
    offer = next(o for s in provider.services for o in s.offers if queries.is_published(o))
    version = queries.latest_version(offer)
    assert version is not None

    # Candidate rows exist post-scan, proving we are not in an empty world.
    candidate_count = session.execute(select(func.count()).select_from(Candidate)).scalar_one()
    assert candidate_count >= 1

    rows = queries.fetch_offer_evidence(session, offer_version_id=version.id)
    assert rows
    assert all(ev.offer_version_id == version.id for ev in rows)

    # A version id that does not exist yields no evidence at all.
    assert queries.fetch_offer_evidence(session, offer_version_id=-1) == []


# --- scope 5: history -------------------------------------------------------


@skip_without_db
def test_offer_history_versions_and_change_events(session: Session) -> None:
    _publish(session)
    provider = _cloudflare(session)
    offer = next(o for s in provider.services for o in s.offers if queries.is_published(o))

    versions = queries.fetch_offer_versions(session, offer_id=offer.id)
    events = queries.fetch_offer_change_events(session, offer_id=offer.id)
    history = service.serialize_offer_history(offer.id, versions, events)

    assert [v.version_number for v in history.versions] == sorted(
        v.version_number for v in history.versions
    )
    assert history.versions
    assert history.change_events
    assert history.change_events[0].change_type == "added"
    assert all(e.publication_status == "published" for e in history.change_events)


# --- scope 6: completeness / freshness --------------------------------------


@skip_without_db
def test_completeness_and_freshness_signals_present(session: Session) -> None:
    _publish(session)
    provider = _cloudflare(session)
    offer = next(o for s in provider.services for o in s.offers if queries.is_published(o))
    cat_map = queries.category_map(session, [offer.service.category_id])
    detail = service.serialize_offer_detail(offer, cat_map)
    assert detail.completeness is not None
    assert detail.freshness is not None
    assert detail.advanced.signals is not None
    assert "completeness" in detail.advanced.signals
    assert "freshness" in detail.advanced.signals
