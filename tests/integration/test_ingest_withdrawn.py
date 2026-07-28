"""Integration proof of the ``withdrawn`` case on real ORM rows (F008 S3).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL.

``withdrawn`` is one of the two F008 *pipeline* cases: it cannot be proved from a
single captured document because it only exists across time. This module drives
it through :func:`tests.support.fixtures.drive_withdrawn` -- scan A observes an
offer, scan B observes the same document with that offer row **absent** -- and
asserts the resulting ``change_event``.

Determinism: every reconcile pass takes an **injected** ``now``. Nothing here
reads the wall clock and nothing sleeps, so the same inputs produce the same rows
on every run, on any machine, at any hour. ``test_withdrawn_is_deterministic``
pins that by driving the whole case twice with two very different frozen clocks
and asserting an identical outcome.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models.domain import ChangeEvent, Offer, OfferVersion, Source
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.support.fixtures import drive_withdrawn

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

ENDPOINT = "https://provider-w.example/offers.json"
DOMAINS = ("provider-w.example",)

#: Two offers in scan A; only the first survives into scan B.
WIDGETS: Mapping[str, object] = {
    "service": "Widgets",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "quotas": [{"metric": "requests", "exhaustion_behaviour": "hard_stop"}],
}
GADGETS: Mapping[str, object] = {
    "service": "Gadgets",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "quotas": [{"metric": "builds", "exhaustion_behaviour": "throttled"}],
}

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


def _make_source(session: Session) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint=ENDPOINT,
        enabled=True,
        schedule="daily",
    )
    session.add(source)
    session.flush()
    return source


@skip_without_db
def test_vanished_offer_is_recorded_as_a_material_withdrawal(session: Session) -> None:
    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    outcome = drive_withdrawn(
        session,
        _make_source(session),
        present=[WIDGETS, GADGETS],
        absent=[WIDGETS],
        domains=DOMAINS,
        now=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
    )

    # drive_withdrawn already asserts the core shape; pin the invariants here too
    # so this file fails loudly if the harness is ever weakened.
    assert outcome.event.change_type == "withdrawn"
    assert outcome.event.materiality == "material"
    assert outcome.event.new_candidate_id is None
    assert outcome.event.previous_candidate_id is not None
    assert outcome.event.publication_status == "draft"
    assert outcome.first_scan_id != outcome.second_scan_id

    # Reconciliation has no publication path: a withdrawal never touches offers.
    assert session.execute(select(func.count()).select_from(Offer)).scalar_one() == offers_before
    assert (
        session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
        == versions_before
    )


@skip_without_db
def test_surviving_offer_is_not_withdrawn(session: Session) -> None:
    """Only the vanished identity is withdrawn -- the survivor is untouched."""

    outcome = drive_withdrawn(
        session,
        _make_source(session),
        present=[WIDGETS, GADGETS],
        absent=[WIDGETS],
        domains=DOMAINS,
        now=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
    )

    withdrawals = list(
        session.execute(select(ChangeEvent).where(ChangeEvent.change_type == "withdrawn")).scalars()
    )
    assert len(withdrawals) == 1
    assert withdrawals[0].id == outcome.event.id


@skip_without_db
@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        datetime(2031, 11, 30, 3, 17, tzinfo=UTC),
    ],
)
def test_withdrawn_is_deterministic_under_any_injected_clock(
    session: Session, now: datetime
) -> None:
    """The same inputs yield the same classification whatever the clock says.

    Two runs five years apart must agree -- proving the case is driven by the
    injected ``now`` and the fixture data alone, never by wall-clock time.
    """

    outcome = drive_withdrawn(
        session,
        _make_source(session),
        present=[WIDGETS, GADGETS],
        absent=[WIDGETS],
        domains=DOMAINS,
        now=now,
    )
    assert (outcome.event.change_type, outcome.event.materiality) == ("withdrawn", "material")
    assert outcome.event.new_candidate_id is None
