"""Live HTTP: the three adviser endpoints must not serve an expired free claim.

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. Unit tests
prove the pure gates; this proves the *deployed request path* -- real routes,
real dependency injection, real session, real ``datetime.now`` -- because a gate
that is correct in isolation and unreachable through the router protects nothing.

EXPIRY MECHANISM (deliberately independent of every earlier derivation)
-----------------------------------------------------------------------
``assess_staleness`` decides ``stale = (now - fetched_at) > window``. Prior
derivations of this defect moved ``age``: one backdated ``Snapshot.fetched_at``,
another advanced the clock past real ingest. **This module moves ``window``
instead.** Both offers below are seeded in the same transaction, share a single
``fetched_at``, and are read at the real wall clock; the only difference between
them is the ``schedule`` their source declares. Nothing is backdated and no clock
is patched, so these tests exercise the router's own ``_now()`` rather than a
test double of it, and an error in age arithmetic could not manufacture the
result.

Every test is a PAIR: the stale direction proves the claim stops being served,
the fresh direction proves the identical code path still serves a genuinely free
offer. Wrongly withdrawing a true free offer is a defect of equal severity.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.adviser.abuse import InMemoryAbuseStore
from app.db import get_session
from app.main import app
from app.models.domain import (
    Category,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    Service,
    Snapshot,
    Source,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import select as sa_select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

CATEGORY_SLUG = "object-file-storage"
STALE_SLUG = "synthetic-expired-evidence"
FRESH_SLUG = "synthetic-current-evidence"

#: A one-second refresh window. The shared fetch instant below is a few seconds
#: in the past, so this window is exceeded and the pair goes stale WITHOUT any
#: per-offer timestamp being moved.
EXPIRED_SCHEDULE = "1s"
#: A thirty-day window: the very same fetch instant is comfortably inside it.
CURRENT_SCHEDULE = "monthly"

#: How far back the SHARED fetch instant sits. It exists only so that a
#: sub-second window can be exceeded at all -- both offers receive this exact
#: value, so it cannot be what distinguishes them. The window is the only
#: variable. Ten seconds is far enough above scheduling jitter to be
#: deterministic and far below the thirty-day window to leave it untouched.
SHARED_FETCH_LAG = timedelta(seconds=10)


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


def _seed(session: Session, slug: str, *, schedule: str, fetched_at: datetime) -> int:
    """One published, genuinely-Z0 offer whose source declares ``schedule``."""

    category_id = session.execute(
        sa_select(Category.id).where(Category.slug == CATEGORY_SLUG)
    ).scalar_one()
    provider = Provider(slug=slug, name=f"{slug} (synthetic)", type="commercial")
    session.add(provider)
    session.flush()
    svc = Service(
        provider_id=provider.id,
        category_id=category_id,
        canonical_name=f"{slug} object store",
        deployment_model="managed",
        portability_traits=["open_source"],
    )
    session.add(svc)
    session.flush()
    offer = Offer(
        service_id=svc.id,
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
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
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        material_facts={
            "confidence": 0.95,
            "confidence_signals": {"completeness": 1.0, "freshness": 1.0},
            "classification": {"zero_cost_class": "Z0_TRUE_FREE", "reasons": []},
            "gate": {"automatic_threshold": 0.90, "uncertain_threshold": 0.70},
        },
    )
    session.add(version)
    session.flush()
    session.add(
        Quota(
            offer_version_id=version.id,
            metric="storage",
            amount=100,
            unit="GB",
            reset_period="month",
            behaviour="hard",
            exhaustion_behaviour="hard_stop",
        )
    )
    source = Source(
        provider_id=provider.id,
        slug=f"{slug}-src",
        adapter_type="html",
        trust_level="official",
        official=True,
        endpoint=f"https://example.invalid/{slug}",
        schedule=schedule,
    )
    session.add(source)
    session.flush()
    snapshot = Snapshot(
        source_id=source.id,
        content_location=f"memory://{slug}",
        mime_type="text/html",
        content_hash=f"snap-{slug}",
        fetched_at=fetched_at,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        Evidence(
            source_id=source.id,
            offer_version_id=version.id,
            snapshot_id=snapshot.id,
            official=True,
            url=f"https://example.invalid/{slug}",
            content_hash=f"ev-{slug}",
        )
    )
    session.flush()
    return int(offer.id)


@pytest.fixture
def client(engine, monkeypatch) -> Iterator[TestClient]:
    """A client over a rolled-back transaction holding BOTH offers.

    Both are seeded from ONE ``fetched_at``, so any behavioural difference
    between them is attributable to the source window and nothing else.
    """

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    fetched_at = datetime.now(UTC) - SHARED_FETCH_LAG
    session.stale_offer_id = _seed(  # type: ignore[attr-defined]
        session, STALE_SLUG, schedule=EXPIRED_SCHEDULE, fetched_at=fetched_at
    )
    session.fresh_offer_id = _seed(  # type: ignore[attr-defined]
        session, FRESH_SLUG, schedule=CURRENT_SCHEDULE, fetched_at=fetched_at
    )
    session.flush()

    store = InMemoryAbuseStore()
    monkeypatch.setattr("app.adviser.router.get_abuse_store", lambda: store)
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)
        session.close()
        transaction.rollback()
        connection.close()


def _request() -> dict:
    return {
        "workload_name": "currency-pair",
        "requirements": [
            {
                "category": CATEGORY_SLUG,
                "demands": [{"metric": "storage", "amount": "5", "unit": "GB", "period": "month"}],
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Instrument floor                                                            #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_the_fixture_really_produces_one_expired_and_one_current_offer(client) -> None:
    """A floor, not a behaviour test.

    If the window trick stopped working, every assertion below would pass
    vacuously ("no stale claim served" because nothing is stale). Fail loudly
    here instead of reporting a comfortable zero.
    """

    from app.adviser.select import gather_candidates

    session = app.dependency_overrides[get_session]()
    pool = gather_candidates(session, now=datetime.now(UTC))

    stale_slugs = {c.provider_slug for c in pool.stale}
    fresh_slugs = {c.provider_slug for c in pool.z0}
    assert STALE_SLUG in stale_slugs, "fixture floor: the expired offer is not stale"
    assert FRESH_SLUG in fresh_slugs, "fixture floor: the current offer is not fresh"


# --------------------------------------------------------------------------- #
# SURFACE 1 -- POST /adviser/recommend                                        #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_recommend_does_not_propose_an_offer_whose_evidence_expired(client) -> None:
    body = client.post("/adviser/recommend", json=_request()).json()

    proposed = [c["offer"]["provider_slug"] for c in body["architecture"]]
    assert STALE_SLUG not in proposed

    # It is named, with a reason -- refused, not hidden.
    blocking = " ".join(i["blocking_reason"] for i in body["impossible"])
    serialized = json.dumps(body)
    if STALE_SLUG in serialized:
        assert "no longer known to be current" in blocking


@skip_without_db
def test_recommend_still_proposes_a_freshly_evidenced_free_offer(client) -> None:
    response = client.post("/adviser/recommend", json=_request())

    assert response.status_code == 200
    body = response.json()
    assert body["fully_zero_cost"] is True
    proposed = [c["offer"]["provider_slug"] for c in body["architecture"]]
    assert proposed == [FRESH_SLUG]


# --------------------------------------------------------------------------- #
# SURFACE 2 -- POST /adviser/recommend/assisted                               #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_assisted_recommend_inherits_the_same_currency_gate(client) -> None:
    """The assisted front door runs the SAME deterministic core.

    Worth its own pair: it is a second entry point into ``gather_candidates``,
    and an entry point that forgot to pass a clock would fail closed rather than
    silently reverting to always-fresh -- but only if something checks.
    """

    response = client.post(
        "/adviser/recommend/assisted",
        json={"description": "I need object file storage for about 5 GB a month"},
    )
    assert response.status_code == 200
    body = response.json()
    recommendation = body.get("recommendation")
    if recommendation is None:
        pytest.skip("the deterministic parser did not interpret the description")

    proposed = [c["offer"]["provider_slug"] for c in recommendation["architecture"]]
    assert STALE_SLUG not in proposed
    assert FRESH_SLUG in proposed


# --------------------------------------------------------------------------- #
# SURFACE 3 -- POST /adviser/export, the artefact that LEAVES THE SYSTEM      #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_exported_bundle_never_carries_an_expired_free_claim(client) -> None:
    response = client.post("/adviser/export", json=_request())
    assert response.status_code == 200
    body = response.json()

    manifest = next(f for f in body["files"] if f["path"] == "MANIFEST.json")
    architecture = json.loads(manifest["content"])["architecture"]
    named = {component["provider_slug"] for component in architecture}

    assert STALE_SLUG not in named
    for component in architecture:
        assert component["zero_cost_class"] == "Z0_TRUE_FREE"

    # And no generated file asserts the expired offer is free.
    for generated in body["files"]:
        for line in generated["content"].splitlines():
            if STALE_SLUG in line and "Z0_TRUE_FREE" in line:
                assert "cannot back a guaranteed-$0 architecture" in line


@skip_without_db
def test_exported_bundle_still_carries_a_freshly_evidenced_free_claim(client) -> None:
    response = client.post("/adviser/export", json=_request())
    assert response.status_code == 200
    body = response.json()

    assert body["manifest"]["fully_zero_cost"] is True
    manifest = next(f for f in body["files"] if f["path"] == "MANIFEST.json")
    architecture = json.loads(manifest["content"])["architecture"]
    assert [c["provider_slug"] for c in architecture] == [FRESH_SLUG]

    readme = next(f for f in body["files"] if f["path"] == "README.md")
    assert "$0 proof" in readme["content"]
