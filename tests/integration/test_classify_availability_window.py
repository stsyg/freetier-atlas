"""Integration proof that the availability window is read from REAL ORM rows.

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL.

The defect this guards was not in the classifier's arithmetic; it was that
``offer.available_from`` travelled the whole way from the column through
:func:`app.classify.orm.offer_facts_from_orm` into ``OfferFacts`` and was then
consulted by NOTHING. A pure unit test over hand-built ``OfferFacts`` cannot see
that class of defect at all, because it starts downstream of the link that was
broken. So this module persists genuine ``Offer`` rows and classifies them
through the ORM adapter.

**Both arms are proved here, deliberately and with equal weight.** A gate that
cannot be shown to PERMIT is indistinguishable from one that broke the product,
and the permit arm is what protects against wrongly WITHHOLDING a genuinely free
offer -- a failure this project weights equally with wrongly asserting one.

Determinism: every classification takes an injected ``as_of``. Nothing here
reads the wall clock, so the same rows produce the same verdict on any machine
at any hour, which is the property PR #100 established.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.classify import Z0_TRUE_FREE, Z2_TEMPORARY_OR_CONDITIONAL, classify_offer
from app.classify.engine import UNKNOWN
from app.models.domain import Offer, OfferVersion, Provider, Quota, Service
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

#: A fixed clock. Every date below is expressed relative to it.
AS_OF = date(2026, 1, 15)

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
    """A session bound to a transaction that is always rolled back."""

    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()


def _seed_offer(
    session: Session,
    *,
    slug: str,
    available_from: date | None = None,
    available_until: date | None = None,
) -> Offer:
    """Persist a provider/service/offer/version/quota whose only variable is the window.

    Every other material fact is explicitly Z0-clean -- no card, no paid
    dependencies, one safe ``hard_stop`` quota -- so the availability window is
    the sole thing that can decide the verdict. If any assertion below moves, it
    moved because of the window.
    """

    provider = Provider(slug=slug, name=f"{slug} (synthetic)", type="cloud")
    session.add(provider)
    session.flush()

    service = Service(
        provider_id=provider.id,
        canonical_name=f"{slug} service",
        deployment_model="managed",
    )
    session.add(service)
    session.flush()

    offer = Offer(
        service_id=service.id,
        offer_type="always_free",
        # Seeded UNKNOWN on purpose: the stored column must not be what the
        # assertions read back, or this test would be checking its own fixture.
        zero_cost_class=UNKNOWN,
        requires_card=False,
        has_paid_dependencies=False,
        available_from=available_from,
        available_until=available_until,
    )
    session.add(offer)
    session.flush()

    version = OfferVersion(
        offer_id=offer.id,
        version_number=1,
        content_hash=f"hash-{slug}",
        offer_type="always_free",
        zero_cost_class=UNKNOWN,
    )
    session.add(version)
    session.flush()

    session.add(
        Quota(
            offer_version_id=version.id,
            metric="requests",
            exhaustion_behaviour="hard_stop",
        )
    )
    session.flush()
    session.refresh(offer)
    return offer


@skip_without_db
def test_a_persisted_future_available_from_is_not_published_as_free(session: Session) -> None:
    """DENY ARM, end to end from the column.

    Before the opening gate this exact row classified ``Z0_TRUE_FREE`` and
    carried the sentence "Usage remains $0" about an offer that does not exist
    yet.
    """

    opens = date(2030, 1, 1)
    offer = _seed_offer(session, slug="not-yet-open-probe", available_from=opens)

    # The column really did round-trip through PostgreSQL.
    assert offer.available_from == opens

    result = classify_offer(offer, as_of=AS_OF)

    assert result.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert result.is_zero_cost is False
    assert any(opens.isoformat() in c for c in result.blocking_conditions)
    assert not any("Usage remains $0" in r for r in result.reasons)


@skip_without_db
def test_a_persisted_past_available_from_still_reaches_z0(session: Session) -> None:
    """PERMIT ARM, end to end. The gate must not withhold an open offer."""

    offer = _seed_offer(session, slug="already-open-probe", available_from=date(2020, 6, 1))
    assert offer.available_from == date(2020, 6, 1)

    result = classify_offer(offer, as_of=AS_OF)

    assert result.zero_cost_class == Z0_TRUE_FREE
    assert result.is_zero_cost is True
    assert not result.blocking_conditions


@skip_without_db
def test_a_persisted_null_available_from_is_unaffected(session: Session) -> None:
    """PERMIT ARM: an offer with no start date behaves exactly as before."""

    offer = _seed_offer(session, slug="no-window-probe")
    assert offer.available_from is None

    result = classify_offer(offer, as_of=AS_OF)

    assert result.zero_cost_class == Z0_TRUE_FREE
    assert not result.blocking_conditions


@skip_without_db
def test_the_persisted_window_verdict_moves_only_when_the_offer_opens(
    session: Session,
) -> None:
    """The same persisted row, classified at three moments.

    This is the property the opening gate adds, measured on real rows rather
    than asserted: the class is Z2 while the offer is shut and Z0 from the day
    it opens, and the boundary is the opening date itself.
    """

    opens = date(2030, 1, 1)
    offer = _seed_offer(session, slug="boundary-probe", available_from=opens)

    before = classify_offer(offer, as_of=date(2029, 12, 31))
    on_the_day = classify_offer(offer, as_of=opens)
    after = classify_offer(offer, as_of=date(2030, 1, 2))

    assert before.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert on_the_day.zero_cost_class == Z0_TRUE_FREE
    assert after.zero_cost_class == Z0_TRUE_FREE


@skip_without_db
def test_a_persisted_contradictory_window_is_unknown(session: Session) -> None:
    """A window that opens after it ends is contradictory, not merely conditional."""

    offer = _seed_offer(
        session,
        slug="contradictory-window-probe",
        available_from=date(2030, 1, 1),
        available_until=date(2025, 6, 1),
    )

    result = classify_offer(offer, as_of=AS_OF)

    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert any("contradictory" in c.lower() for c in result.blocking_conditions)
