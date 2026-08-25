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
from datetime import UTC, datetime
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
    ProviderCategoryCoverage,
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


def _category(session: Session, slug: str, name: str) -> Category:
    """Resolve a canonical category, tolerating the 0010 seed already owning it."""

    existing = session.execute(select(Category).where(Category.slug == slug)).scalar_one_or_none()
    if existing is not None:
        return existing
    created = Category(slug=slug, name=name)
    session.add(created)
    session.flush()
    return created


def _seed_synthetic(session: Session) -> dict:
    """Insert two clearly-synthetic providers with categorized published offers.

    Returns a dict of the created ids. All rows live only in the rolled-back test
    transaction; nothing is committed or published.
    """

    storage = _category(session, "object-file-storage", "Object and file storage")
    serverless = _category(session, "serverless-functions", "Serverless functions")
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
    context = queries.coverage_signal_context(session, providers)
    matrix = service.serialize_category_matrix(providers, cat_map, context)

    assert len(matrix.categories) == 14
    assert [row.ordinal for row in matrix.categories] == list(range(1, 15))
    assert {"example-alpha", "example-beta"}.issubset(set(matrix.provider_slugs))

    storage_row = next(r for r in matrix.categories if r.slug == "object-file-storage")
    coverage = {c.provider_slug: c for c in storage_row.providers}
    # alpha has a published Z0 offer here, so the DERIVED state is verified_free.
    assert coverage["example-alpha"].derived_state == "verified_free"
    assert coverage["example-alpha"].free_offer_count == 1
    # beta has published nothing here. Before F008 S2 that was GUESSED as
    # "not_offered"; with no declaration on file the honest answer is "unknown".
    assert coverage["example-beta"].published_offer_count == 0
    assert coverage["example-beta"].derived_state == "unknown"
    assert coverage["example-beta"].state == "unknown"
    assert coverage["example-beta"].state != "not_offered"
    assert coverage["example-beta"].declared_state is None

    serverless_row = next(r for r in matrix.categories if r.slug == "serverless-functions")
    coverage = {c.provider_slug: c for c in serverless_row.providers}
    # example-beta offers a non-free serverless offer.
    assert coverage["example-beta"].derived_state == "offered_no_z0"
    assert coverage["example-beta"].free_offer_count == 0


@skip_without_db
def test_category_matrix_never_guesses_not_offered_from_zero_published(
    session: Session,
) -> None:
    """F008 acceptance step 2: absence of offers is never evidence of absence.

    Every undeclared pair -- and there are many, since the synthetic providers
    declare no coverage at all -- must report ``unknown``. If any of them says
    ``not_offered`` the guess has been re-introduced.
    """

    _publish(session)
    _seed_synthetic(session)

    providers = queries.fetch_providers(session)
    cat_map = queries.category_map_for_providers(session, providers)
    context = queries.coverage_signal_context(session, providers)
    matrix = service.serialize_category_matrix(providers, cat_map, context)

    empty_pairs = [
        c
        for row in matrix.categories
        for c in row.providers
        if c.published_offer_count == 0 and c.declared_state is None
    ]
    assert empty_pairs, "the fixture must contain at least one empty undeclared pair"
    for c in empty_pairs:
        assert c.state == "unknown"
        assert c.derived_state == "unknown"
        assert c.state != "not_offered"

    # not_offered can only ever be DECLARED, so nothing here may report it.
    assert not [
        c for row in matrix.categories for c in row.providers if c.derived_state == "not_offered"
    ]


@skip_without_db
def test_declared_coverage_is_served_and_a_contradiction_is_flagged(
    session: Session,
) -> None:
    """A declaration is served verbatim; a declaration that denies a published
    Z0 offer is surfaced as ``conflicting`` rather than silently believed."""

    _publish(session)
    _seed_synthetic(session)

    providers = queries.fetch_providers(session)
    alpha = next(p for p in providers if p.slug == "example-alpha")
    storage_id = session.execute(
        select(Category.id).where(Category.slug == "object-file-storage")
    ).scalar_one()
    compute_id = session.execute(
        select(Category.id).where(Category.slug == "compute-vms")
    ).scalar_one()

    session.add_all(
        [
            # Honest: alpha declines to offer compute, and says why.
            ProviderCategoryCoverage(
                provider_id=alpha.id,
                category_id=compute_id,
                state="not_offered",
                rationale="example-alpha ships no compute product line.",
            ),
            # Dishonest: alpha claims ignorance over its own published Z0 offer.
            ProviderCategoryCoverage(
                provider_id=alpha.id,
                category_id=storage_id,
                state="unknown",
            ),
        ]
    )
    session.flush()

    cat_map = queries.category_map_for_providers(session, providers)
    context = queries.coverage_signal_context(session, providers)
    matrix = service.serialize_category_matrix(providers, cat_map, context)

    compute = next(
        c
        for row in matrix.categories
        if row.slug == "compute-vms"
        for c in row.providers
        if c.provider_slug == "example-alpha"
    )
    assert compute.declared_state == "not_offered"
    assert compute.state == "not_offered"
    assert compute.derived_state == "unknown"
    assert compute.mismatch is False
    assert compute.rationale == "example-alpha ships no compute product line."

    storage = next(
        c
        for row in matrix.categories
        if row.slug == "object-file-storage"
        for c in row.providers
        if c.provider_slug == "example-alpha"
    )
    assert storage.declared_state == "unknown"
    assert storage.derived_state == "verified_free"
    assert storage.mismatch is True
    assert storage.state == "conflicting", "an unknown over a published Z0 offer is a conflict"


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
    compare = service.serialize_compare(
        ordered, resolved, cat_map, queries.currency_context(session, now=datetime.now(UTC))
    )

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
    #
    # These synthetic offers are seeded with a published version but NO Evidence
    # rows at all, so there is no snapshot, no fetch time, and therefore nothing
    # to check currency against. The persisted confidence is 0.93 -- which would
    # once have been served as "high" -- but a free claim with no checkable
    # evidence behind it may not carry earned confidence, so the label collapses
    # to "unknown" and the numeric score is withheld entirely.
    #
    # Note the asymmetry that keeps this honest: "we could not check" is refused,
    # but it is NOT reported as stale. Absence of evidence is not evidence of
    # expiry.
    dumped = alpha.model_dump()
    assert dumped["confidence_label"] == "unknown"
    assert "confidence" not in dumped
    assert dumped["advanced"]["score"] is None
    assert dumped["evidence_currency"]["checked"] is False
    assert dumped["evidence_currency"]["stale"] is False
    assert dumped["evidence_currency"]["current"] is False
    # None, never 0.0: an absent measurement is not a zero score.
    assert dumped["evidence_currency"]["freshness"] is None
    assert "cannot be established" in dumped["evidence_currency"]["reason"]

    # PAIRED CONTROL: the published Cloudflare offer in the SAME comparison does
    # carry real, freshly-fetched official evidence, and still reads "high" with
    # its numeric score intact. Without this the assertions above could equally
    # be satisfied by a gate that refuses everything.
    cf_provider = queries.fetch_provider(session, "cloudflare")
    assert cf_provider is not None
    cf_offer = next(o for s in cf_provider.services for o in s.offers if queries.is_published(o))
    cf_compare = service.serialize_compare(
        [cf_offer.id],
        [cf_offer],
        queries.category_map(session, [cf_offer.service.category_id]),
        queries.currency_context(session, now=datetime.now(UTC)),
    )
    cf_dumped = cf_compare.offers[0].model_dump()
    assert cf_dumped["confidence_label"] == "high"
    assert cf_dumped["advanced"]["score"] >= 0.90
    assert cf_dumped["evidence_currency"]["current"] is True
    assert cf_dumped["evidence_currency"]["checked"] is True
    assert cf_dumped["evidence_currency"]["freshness"] is not None
    assert cf_dumped["evidence_currency"]["reason"] is None


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
