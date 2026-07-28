"""Integration proof of the ``stale`` case on real ORM rows (F008 S3).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL.

``stale`` is the second F008 *pipeline* case. It is driven through
:func:`tests.support.fixtures.drive_stale` with a **frozen clock** derived from
the source's own ``schedule`` window -- never ``sleep()``, never
``datetime.now()``. The proof is three-part:

1. :func:`~app.ingest.reconcile.assess_staleness` reports ``stale``;
2. reconciliation flags every candidate ``verification_state='stale'``;
3. the publication gate **does not publish** -- the ``fresh`` hard condition
   fails, so the candidate is withheld or routed to a pending review item.

``test_stale_threshold_is_the_schedule_window`` pins the boundary from both
sides: one second inside the window is fresh and one second outside is stale, so
the assertion cannot pass by accident on a machine whose clock happens to be
convenient.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.models import PublishingSection
from app.ingest.reconcile import (
    DEFAULT_STALENESS_WINDOW,
    assess_staleness,
    parse_schedule_window,
)
from app.models.domain import Offer, OfferVersion, Provider, Source
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.support.fixtures import drive_stale, stale_clock

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

ENDPOINT = "https://provider-s.example/offers.json"
DOMAINS = ("provider-s.example",)

PUBLISHING = PublishingSection(
    automatic_threshold=0.90,
    uncertain_threshold=0.70,
    require_official_source=True,
    require_deterministic_numeric_validation=True,
)

WIDGETS: Mapping[str, object] = {
    "service": "Widgets",
    "offer_type": "always_free",
    "requires_card": False,
    "has_paid_dependencies": False,
    "quotas": [{"metric": "requests", "exhaustion_behaviour": "hard_stop"}],
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


def _make_source(
    session: Session, *, schedule: str | None = "daily", slug: str = "stale-example"
) -> Source:
    provider = Provider(slug=slug, name=slug.replace("-", " ").title(), type="cloud")
    session.add(provider)
    session.flush()
    source = Source(
        provider_id=provider.id,
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint=ENDPOINT,
        enabled=True,
        schedule=schedule,
    )
    session.add(source)
    session.flush()
    return source


@skip_without_db
def test_stale_source_is_flagged_and_never_published(session: Session) -> None:
    offers_before = session.execute(select(func.count()).select_from(Offer)).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    outcome = drive_stale(
        session,
        _make_source(session),
        PUBLISHING,
        offers=[WIDGETS],
        domains=DOMAINS,
    )

    assert outcome.staleness.stale is True
    assert outcome.staleness.age > outcome.staleness.window
    assert outcome.candidates
    assert all(c.verification_state == "stale" for c in outcome.candidates)
    assert outcome.published == 0
    assert outcome.withheld + outcome.reviewed >= 1

    # Nothing was published, so the immutable version history is untouched.
    assert session.execute(select(func.count()).select_from(Offer)).scalar_one() == offers_before
    assert (
        session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
        == versions_before
    )


@skip_without_db
def test_stale_outcome_is_identical_on_repeat_runs(session: Session) -> None:
    """Two runs of the same case produce the same verdict.

    The clock is derived from each scan's own snapshot rather than from a
    hard-coded date, so this holds whatever time the suite is actually executed
    at -- there is neither a wall-clock comparison to flake on nor a literal that
    silently drifts into the past as the calendar moves.

    The two runs are genuinely independent (distinct providers), so the second is
    not shielded by the publisher's identity-level review dedup.
    """

    verdicts = []
    for run in range(2):
        outcome = drive_stale(
            session,
            _make_source(session, slug=f"stale-example-run{run}"),
            PUBLISHING,
            offers=[WIDGETS],
            domains=DOMAINS,
        )
        verdicts.append(
            (
                outcome.staleness.stale,
                outcome.staleness.window,
                outcome.published,
                outcome.withheld,
                outcome.reviewed,
                tuple(sorted(c.verification_state for c in outcome.candidates)),
            )
        )
    assert verdicts[0] == verdicts[1]


@skip_without_db
@pytest.mark.parametrize("schedule", ["daily", "weekly", None])
def test_stale_threshold_is_the_source_schedule_window(
    session: Session, schedule: str | None
) -> None:
    """The boundary is the source's own window, pinned from both sides.

    A ``None`` schedule falls back to :data:`DEFAULT_STALENESS_WINDOW` (7 days);
    it is never treated as "always fresh".
    """

    source = _make_source(session, schedule=schedule)
    window = parse_schedule_window(schedule, default=DEFAULT_STALENESS_WINDOW)
    fetched_at = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

    inside = assess_staleness(fetched_at, fetched_at + window - timedelta(seconds=1), schedule)
    outside = assess_staleness(fetched_at, fetched_at + window + timedelta(seconds=1), schedule)
    assert inside.stale is False
    assert outside.stale is True
    assert inside.window == outside.window == window

    # stale_clock always lands on the stale side of that same boundary.
    assert assess_staleness(fetched_at, stale_clock(source, fetched_at), schedule).stale is True
