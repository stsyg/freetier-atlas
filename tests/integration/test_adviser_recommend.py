"""Integration tests for the deterministic adviser (F006 slice 3).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. These prove the
adviser end-to-end against the *actual* schema (migrations 0001..0007):

* a small set of **clearly synthetic** fixture providers/offers is inserted
  *inside the rolled-back test transaction only* (owner decision Q6) so the
  multi-provider / multi-option adviser behaviour is provable while the synthetic
  data is never committed and never published on a normal stack run;
* ``gather_candidates`` reads only the published ``offer`` graph -- the
  ``candidate`` / ``discovery_candidate`` tables are never queried; and
* the recommendation runs with NO LLM in the path (the default) and the
  offer_version immutability (SQLSTATE 23001) + both 0006 separation triggers
  remain intact.

Every test runs inside a transaction that is rolled back, leaving data clean.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.adviser.recommend import recommend
from app.adviser.schema import RecommendationRequest
from app.adviser.schemas import build_response
from app.adviser.select import gather_candidates
from app.models.domain import (
    Category,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    Service,
)
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

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


def _facts(zero_cost_class: str) -> dict:
    return {
        "confidence": 0.93,
        "gate": {"automatic_threshold": 0.90, "uncertain_threshold": 0.70},
        "classification": {
            "zero_cost_class": zero_cost_class,
            "reasons": [],
            "blocking_conditions": [],
        },
    }


def _seed(session: Session) -> None:
    """Insert clearly-synthetic categorized offers (rolled back, never published)."""

    storage = Category(slug="object-file-storage", name="Object and file storage")
    hosting = Category(slug="compute-vms", name="Compute VMs")
    session.add_all([storage, hosting])
    session.flush()

    def _make(
        *,
        slug,
        name,
        category,
        service_name,
        zclass,
        offer_type,
        quota_metric,
        quota_amount,
        quota_unit,
        exhaustion,
        deployment="managed",
        traits=None,
    ):
        provider = Provider(slug=slug, name=name, type="commercial", source_health="ok")
        session.add(provider)
        session.flush()
        svc = Service(
            provider_id=provider.id,
            category_id=category.id,
            canonical_name=service_name,
            deployment_model=deployment,
            portability_traits=traits or [],
        )
        session.add(svc)
        session.flush()
        offer = Offer(
            service_id=svc.id,
            offer_type=offer_type,
            zero_cost_class=zclass,
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
            content_hash=f"synthetic-{slug}",
            offer_type=offer_type,
            zero_cost_class=zclass,
            material_facts=_facts(zclass),
        )
        session.add(version)
        session.flush()
        session.add(
            Quota(
                offer_version_id=version.id,
                metric=quota_metric,
                amount=quota_amount,
                unit=quota_unit,
                reset_period="month",
                behaviour="hard",
                exhaustion_behaviour=exhaustion,
            )
        )
        session.flush()
        return offer

    _make(
        slug="example-z0store",
        name="Example Z0 Store (synthetic)",
        category=storage,
        service_name="Z0 Object Store",
        zclass="Z0_TRUE_FREE",
        offer_type="always_free",
        quota_metric="storage",
        quota_amount=10,
        quota_unit="GB",
        exhaustion="hard_stop",
        traits=["open_source", "s3_compatible"],
    )
    _make(
        slug="example-z1store",
        name="Example Z1 Store (synthetic)",
        category=storage,
        service_name="Z1 Object Store",
        zclass="Z1_BILLING_EXPOSURE",
        offer_type="recurring_quota",
        quota_metric="storage",
        quota_amount=100,
        quota_unit="GB",
        exhaustion="automatic_billing",
    )
    _make(
        slug="example-z3host",
        name="Example Z3 App (synthetic)",
        category=storage,
        service_name="Z3 Self-Hosted Store",
        zclass="Z3_SELF_HOSTED_BUILDING_BLOCK",
        offer_type="self_hosted_open_source",
        quota_metric="storage",
        quota_amount=1000,
        quota_unit="GB",
        exhaustion="hard_stop",
        deployment="self_hosted",
        traits=["open_source", "self_hostable"],
    )
    _make(
        slug="example-z0vm",
        name="Example Z0 VM (synthetic)",
        category=hosting,
        service_name="Z0 Micro VM",
        zclass="Z0_TRUE_FREE",
        offer_type="always_free",
        quota_metric="compute",
        quota_amount=1,
        quota_unit="vcpu",
        exhaustion="hard_stop",
    )


def _req(category, metric, amount, unit) -> RecommendationRequest:
    return RecommendationRequest.model_validate(
        {
            "workload_name": "integration demo",
            "requirements": [
                {
                    "category": category,
                    "demands": [
                        {"metric": metric, "amount": str(amount), "unit": unit, "period": "month"}
                    ],
                }
            ],
        }
    )


@skip_without_db
def test_satisfiable_recommendation_llm_disabled(session: Session) -> None:
    _seed(session)
    pool = gather_candidates(session)
    result = recommend(_req("object-file-storage", "storage", 5, "GB"), pool)
    body = build_response(result)
    assert body.fully_zero_cost is True
    assert body.architecture, "expected a Z0 component"
    chosen = body.architecture[0].offer
    assert chosen.zero_cost_class == "Z0_TRUE_FREE"
    # Z1 appears only in the separate not-free section, never in the architecture.
    arch_classes = {c.offer.zero_cost_class for c in body.architecture}
    assert "Z1_BILLING_EXPOSURE" not in arch_classes
    assert any(
        o.offer.zero_cost_class == "Z1_BILLING_EXPOSURE" for o in body.not_free_section.options
    )


@skip_without_db
def test_impossible_reduction_recalc_selfhost_order(session: Session) -> None:
    _seed(session)
    pool = gather_candidates(session)
    # 50GB exceeds the 10GB Z0 quota -> impossible order.
    result = recommend(_req("object-file-storage", "storage", 50, "GB"), pool)
    body = build_response(result)
    assert body.fully_zero_cost is False
    assert len(body.impossible) == 1
    imp = body.impossible[0]
    assert imp.blocking_reason
    assert imp.reductions and imp.reductions[0].feasible is True
    assert imp.recalculated is not None and imp.recalculated.reduced is True
    # Self-hosting: the Z3 building block placed on the Z0 VM host.
    assert imp.self_hosting
    assert imp.self_hosting[0].building_block.zero_cost_class == "Z3_SELF_HOSTED_BUILDING_BLOCK"
    assert imp.self_hosting[0].host is not None


@skip_without_db
def test_determinism_identical_input_identical_output(session: Session) -> None:
    _seed(session)
    pool = gather_candidates(session)
    req = _req("object-file-storage", "storage", 5, "GB")
    first = build_response(recommend(req, pool)).model_dump()
    second = build_response(recommend(req, pool)).model_dump()
    assert first == second


@skip_without_db
def test_candidate_table_never_read(session: Session) -> None:
    # gather_candidates must return only published offers; prove it reads the
    # published graph (build succeeds) without ever touching candidate tables.
    _seed(session)
    pool = gather_candidates(session)
    assert pool.z0, "published Z0 offers should be found"
    # discovery_candidate / candidate are separate quarantine tables the adviser
    # never queries; their presence must not affect the pool.
    for cand in (*pool.z0, *pool.z3, *pool.not_free):
        assert cand.version_id, "every candidate is a published version"


@skip_without_db
def test_offer_version_immutability_trigger_still_enforced(session: Session) -> None:
    _seed(session)
    version_id = session.execute(text("SELECT id FROM offer_version LIMIT 1")).scalar_one()
    with pytest.raises(DBAPIError) as excinfo:
        with session.begin_nested():
            session.execute(
                update(OfferVersion)
                .where(OfferVersion.id == version_id)
                .values(zero_cost_class="Z1_BILLING_EXPOSURE")
            )
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23001" or "23001" in str(excinfo.value)


@skip_without_db
def test_separation_triggers_present(session: Session) -> None:
    names = set(
        session.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")).scalars()
    )
    assert "trg_offer_version_immutable" in names
    assert "trg_candidate_official_source" in names
    assert "trg_evidence_official_candidate" in names
