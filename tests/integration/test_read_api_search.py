"""Integration tests for the F006 catalogue query API (search + categories + compare).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. These prove the
new query capabilities end-to-end against the *actual* schema (migrations
0001..0007):

* the real Cloudflare catalogue is published via the S1 scan + S2 gated
  publication path, and
* a small set of **clearly synthetic** fixture providers/offers is inserted
  *inside the rolled-back test transaction only* (owner decision Q6) so
  multi-provider search / matrix / compare behaviour is provable while only
  Cloudflare is genuinely published. The synthetic data is never committed and is
  never published on a normal stack run.

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
    Category,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    Service,
)
from app.read_api import queries, search, service
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
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
    run_provider_scans(session, config, fetcher, publish=True)
    session.flush()
    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"


# --------------------------------------------------------------------------- #
# Synthetic, clearly-fixture-only multi-provider seeding (rolled back)        #
# --------------------------------------------------------------------------- #


def _facts(score: float, zero_cost_class: str, reasons: list[str]) -> dict:
    return {
        "confidence": score,
        "confidence_signals": {"completeness": 0.75, "freshness": 0.8},
        "classification": {
            "zero_cost_class": zero_cost_class,
            "reasons": reasons,
            "blocking_conditions": [],
        },
        "gate": {"automatic_threshold": 0.90, "uncertain_threshold": 0.70},
    }


def _seed_synthetic(session: Session) -> dict:
    """Insert two clearly-synthetic providers with categorized published offers.

    Returns a dict of the created ids. All rows live only in the rolled-back test
    transaction; nothing is committed or published.
    """

    storage = Category(slug="object-file-storage", name="Object and file storage")
    serverless = Category(slug="serverless-functions", name="Serverless functions")
    session.add_all([storage, serverless])
    session.flush()

    def _make(
        *,
        provider_slug: str,
        provider_name: str,
        category: Category,
        service_name: str,
        zero_cost_class: str,
        quota_amount: float,
        quota_unit: str,
    ) -> Offer:
        provider = Provider(
            slug=provider_slug, name=provider_name, type="commercial", source_health="ok"
        )
        session.add(provider)
        session.flush()
        svc = Service(
            provider_id=provider.id,
            category_id=category.id,
            canonical_name=service_name,
            deployment_model="managed",
        )
        session.add(svc)
        session.flush()
        offer = Offer(
            service_id=svc.id,
            offer_type="always_free",
            zero_cost_class=zero_cost_class,
            status="active",
            requires_card=False,
            has_paid_dependencies=False,
            commercial_use_allowed=True,
            personal_use_allowed=True,
        )
        session.add(offer)
        session.flush()
        version = OfferVersion(
            offer_id=offer.id,
            version_number=1,
            content_hash=f"synthetic-{provider_slug}",
            offer_type="always_free",
            zero_cost_class=zero_cost_class,
            material_facts=_facts(0.93, zero_cost_class, ["synthetic fixture reason"]),
        )
        session.add(version)
        session.flush()
        session.add(
            Quota(
                offer_version_id=version.id,
                metric="storage",
                amount=quota_amount,
                unit=quota_unit,
                reset_period="month",
                behaviour="hard",
                exhaustion_behaviour="hard_stop",
            )
        )
        session.flush()
        return offer

    alpha = _make(
        provider_slug="example-alpha",
        provider_name="Example Alpha (synthetic)",
        category=storage,
        service_name="Alpha Object Store",
        zero_cost_class="Z0_TRUE_FREE",
        quota_amount=10,
        quota_unit="GB",
    )
    beta = _make(
        provider_slug="example-beta",
        provider_name="Example Beta (synthetic)",
        category=serverless,
        service_name="Beta Functions",
        zero_cost_class="Z1_BILLING_EXPOSURE",
        quota_amount=3,
        quota_unit="vcpu-hours",  # deliberately unnormalizable -> fail closed
    )
    return {"alpha_offer_id": alpha.id, "beta_offer_id": beta.id}


# --------------------------------------------------------------------------- #
# Search                                                                      #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_search_matches_keyword_and_composes_filters(session: Session) -> None:
    _publish(session)
    _seed_synthetic(session)

    # Keyword search finds the synthetic provider by name.
    params = search.build_params(q="Example")
    page = search.search_published_offers(session, params)
    provider_slugs = {o.service.provider.slug for o in page.offers}
    assert {"example-alpha", "example-beta"}.issubset(provider_slugs)
    # Deterministic ordering by (provider slug, service name, offer id).
    ordering = [(o.service.provider.slug, o.service.canonical_name, o.id) for o in page.offers]
    assert ordering == sorted(ordering)

    # Filters compose: provider + zero_cost_class narrows to exactly one offer.
    params = search.build_params(provider="example-alpha", zero_cost_class="Z0_TRUE_FREE")
    page = search.search_published_offers(session, params)
    assert [o.service.provider.slug for o in page.offers] == ["example-alpha"]

    # A filter that matches nothing published returns an empty, honest result.
    params = search.build_params(provider="example-beta", zero_cost_class="Z0_TRUE_FREE")
    page = search.search_published_offers(session, params)
    assert page.offers == []
    assert page.total == 0


@skip_without_db
def test_search_returns_only_published_never_candidate(session: Session) -> None:
    _publish(session)
    _seed_synthetic(session)

    # Candidates exist post-scan, proving we are not in an empty world.
    candidate_count = session.execute(select(func.count()).select_from(Candidate)).scalar_one()
    assert candidate_count >= 1

    params = search.build_params()
    page = search.search_published_offers(session, params)
    assert page.offers
    # Every returned offer is genuinely published (has an immutable version).
    assert all(queries.is_published(o) for o in page.offers)


@skip_without_db
def test_search_hostile_q_is_neutralised(session: Session) -> None:
    _publish(session)
    _seed_synthetic(session)
    # A LIKE-wildcard-laden / URL-ish q is matched literally, never as a pattern,
    # and never fetched: it simply matches nothing.
    for hostile in ("100%_off", "https://evil.example", "'; DROP TABLE offer;--"):
        params = search.build_params(q=hostile)
        page = search.search_published_offers(session, params)
        assert page.total == 0


# --------------------------------------------------------------------------- #
# Category coverage matrix                                                     #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_category_matrix_is_14_and_multi_provider(session: Session) -> None:
    _publish(session)
    _seed_synthetic(session)

    providers = queries.fetch_providers(session)
    cat_map = queries.category_map_for_providers(session, providers)
    matrix = service.serialize_category_matrix(providers, cat_map)

    assert len(matrix.categories) == 14
    assert [row.ordinal for row in matrix.categories] == list(range(1, 15))
    assert {"example-alpha", "example-beta"}.issubset(set(matrix.provider_slugs))

    storage_row = next(r for r in matrix.categories if r.slug == "object-file-storage")
    coverage = {c.provider_slug: c for c in storage_row.providers}
    assert coverage["example-alpha"].state == "verified_free"
    assert coverage["example-alpha"].free_offer_count == 1
    assert coverage["example-beta"].state == "not_offered"

    serverless_row = next(r for r in matrix.categories if r.slug == "serverless-functions")
    coverage = {c.provider_slug: c for c in serverless_row.providers}
    # example-beta offers a non-free serverless offer.
    assert coverage["example-beta"].state == "no_free_tier"
    assert coverage["example-beta"].free_offer_count == 0


# --------------------------------------------------------------------------- #
# Compare                                                                      #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_compare_normalizes_across_providers_and_fails_closed(session: Session) -> None:
    _publish(session)
    ids = _seed_synthetic(session)
    ordered = [ids["alpha_offer_id"], ids["beta_offer_id"]]

    offer_map = queries.fetch_offers_by_ids(session, ordered)
    resolved = [offer_map[i] for i in ordered]
    cat_map = queries.category_map(session, [o.service.category_id for o in resolved])
    compare = service.serialize_compare(ordered, resolved, cat_map)

    assert [o.offer_id for o in compare.offers] == ordered
    assert {o.provider_slug for o in compare.offers} == {"example-alpha", "example-beta"}

    # alpha's 10 GB storage quota normalizes to bytes.
    alpha = next(o for o in compare.offers if o.provider_slug == "example-alpha")
    alpha_quota = alpha.quotas[0]
    assert alpha_quota.normalized is True
    assert alpha_quota.canonical_unit == "byte"
    assert alpha_quota.canonical_amount == pytest.approx(10 * 1000**3)

    # beta's vcpu-hours quota cannot be normalized -> fails closed.
    beta = next(o for o in compare.offers if o.provider_slug == "example-beta")
    beta_quota = beta.quotas[0]
    assert beta_quota.normalized is False
    assert beta_quota.canonical_amount is None
    assert beta_quota.normalization_note

    # Confidence stays label-primary; numeric only in advanced{}.
    dumped = alpha.model_dump()
    assert dumped["confidence_label"] == "high"
    assert "confidence" not in dumped
    assert dumped["advanced"]["score"] >= 0.90


# --------------------------------------------------------------------------- #
# Invariants: immutability trigger untouched                                  #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_offer_version_immutability_trigger_still_enforced(session: Session) -> None:
    _publish(session)
    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None
    offer = next(o for s in provider.services for o in s.offers if queries.is_published(o))
    version = queries.latest_version(offer)
    assert version is not None

    # The 0005 immutability trigger (SQLSTATE 23001) must still reject any UPDATE
    # to a persisted offer_version. Use a savepoint so the failure is contained.
    with pytest.raises(DBAPIError) as excinfo:
        with session.begin_nested():
            session.execute(
                update(OfferVersion)
                .where(OfferVersion.id == version.id)
                .values(zero_cost_class="Z1_BILLING_EXPOSURE")
            )
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23001" or "23001" in str(excinfo.value)


@skip_without_db
def test_separation_triggers_present(session: Session) -> None:
    # Guard: both 0006 separation triggers exist on the evidence/candidate tables.
    names = set(
        session.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")).scalars()
    )
    assert "trg_candidate_official_source" in names
    assert "trg_evidence_official_candidate" in names
