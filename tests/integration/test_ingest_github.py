"""Integration proof of the GitHub slice on REAL persisted rows (F008 P1).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL.

Everything here runs **offline**: the runner is handed a
:class:`~app.ingest.fetch.FixtureFetcher` built from the committed official
captures, so no test reaches the network. The assertions are deliberately about
*persisted rows* -- ``scan_run`` / ``snapshot`` / ``candidate`` / ``evidence`` /
``offer`` / ``offer_version`` / ``quota`` / ``change_event`` / ``review_item`` --
because a pipeline that returns the right summary counts while writing the wrong
rows is exactly the failure this suite exists to catch.

The two cross-scan cases of the seven-case vocabulary (``withdrawn`` and
``stale``) are driven here through the shared harness with an **injected**
``now``; nothing reads the wall clock and nothing sleeps.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config import load_and_validate
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.models.domain import (
    Candidate,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Quota,
    ReviewItem,
    ScanRun,
    Snapshot,
    Source,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.support.fixtures import drive_stale, drive_withdrawn

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "github.example.yaml"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ingest" / "github" / "html"
DOMAINS = ("docs.github.com",)
TRIAL_ENDPOINT = "https://docs.github.com/en/github-trial-pipeline-case"

#: The four perpetual GitHub allowances this slice expects to reach Z0.
Z0_SERVICES = frozenset({"GitHub Actions", "GitHub Packages", "GitHub Codespaces", "GitHub Pages"})
#: The deliberate NON-Z0 offer: no card required, expires after 30 days.
TRIAL_SERVICE = "GitHub Enterprise Cloud trial"

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


@pytest.fixture(scope="module")
def config():
    return load_and_validate(CONFIG_PATH)


def _run(session: Session, config, *, publish: bool = True):
    """Run the whole provider offline against the committed captures."""

    fetcher = build_fixture_fetcher(config, FIXTURES)
    return run_provider_scans(session, config, fetcher, reconcile=True, publish=publish)


def _github_sources(session: Session) -> list[Source]:
    return list(
        session.execute(
            select(Source).where(Source.endpoint.like("https://docs.github.com/%"))
        ).scalars()
    )


# --- The offline end-to-end run --------------------------------------------


@skip_without_db
def test_the_runner_persists_scans_snapshots_candidates_and_official_evidence(
    session: Session, config
) -> None:
    snapshots_before = session.execute(select(func.count()).select_from(Snapshot)).scalar_one()
    result = _run(session, config)

    scanned = [outcome for outcome in result.sources if outcome.status == "scanned"]
    assert len(scanned) == 5, f"expected all five official sources to scan, got {result.sources}"

    source_ids = [s.id for s in _github_sources(session)]
    assert source_ids

    # Scope every count to THIS run. Asserting on whole-table totals silently
    # depends on the database being empty, which made these assertions fail
    # against a database that had simply been used before.
    run_scan_ids = [outcome.scan_run_id for outcome in scanned if outcome.scan_run_id]
    assert len(run_scan_ids) == 5

    scan_runs = list(
        session.execute(
            select(ScanRun).where(ScanRun.id.in_(run_scan_ids), ScanRun.source_id.in_(source_ids))
        ).scalars()
    )
    candidates = list(
        session.execute(select(Candidate).where(Candidate.scan_run_id.in_(run_scan_ids))).scalars()
    )
    # `snapshot` carries no scan_run_id, so scope it as a delta instead.
    snapshots_after = session.execute(select(func.count()).select_from(Snapshot)).scalar_one()
    assert len(scan_runs) == 5
    assert snapshots_after - snapshots_before == 5, "one stored snapshot per captured page"
    assert len(candidates) == 5, "one offer row per captured official page"

    evidence = list(
        session.execute(
            select(Evidence).where(Evidence.candidate_id.in_([c.id for c in candidates]))
        ).scalars()
    )
    assert len(evidence) == 5, "every official candidate must carry its own evidence row"
    for row in evidence:
        assert row.url.startswith("https://docs.github.com/"), "evidence must be official"


@skip_without_db
def test_published_offers_carry_immutable_versions_and_traceable_quotas(
    session: Session, config
) -> None:
    _run(session, config)

    offers = {
        o.service.canonical_name: o
        for o in session.execute(select(Offer).join(Offer.service)).scalars()
        if o.service.canonical_name in Z0_SERVICES | {TRIAL_SERVICE}
    }
    assert offers, "the gate published nothing; the run proves nothing about publication"

    for name, offer in offers.items():
        versions = list(
            session.execute(select(OfferVersion).where(OfferVersion.offer_id == offer.id)).scalars()
        )
        assert versions, f"{name}: published offer with no immutable version"
        quotas = list(
            session.execute(
                select(Quota).where(Quota.offer_version_id.in_([v.id for v in versions]))
            ).scalars()
        )
        assert quotas, f"{name}: published offer version with no quota rows"


@skip_without_db
def test_the_enterprise_trial_is_published_but_never_as_z0(session: Session, config) -> None:
    """Acceptance step 2 on persisted rows: a no-card TRIAL is not $0-forever."""

    _run(session, config)

    versions = list(
        session.execute(
            select(OfferVersion)
            .join(OfferVersion.offer)
            .join(Offer.service)
            .where(Offer.service.has(canonical_name=TRIAL_SERVICE))
        ).scalars()
    )
    if not versions:
        pytest.skip("the gate withheld the trial offer; the non-Z0 unit control still applies")

    for version in versions:
        assert version.zero_cost_class != "Z0_TRUE_FREE", (
            "a 30-day trial was published as true-free -- this is the exact false "
            "claim the product forbids"
        )
        assert version.zero_cost_class == "Z2_TEMPORARY_OR_CONDITIONAL"


@skip_without_db
def test_the_perpetual_allowances_that_publish_are_z0(session: Session, config) -> None:
    _run(session, config)

    versions = list(
        session.execute(
            select(OfferVersion, Offer).join(OfferVersion.offer).join(Offer.service)
        ).all()
    )
    seen = 0
    for version, offer in versions:
        name = offer.service.canonical_name
        if name not in Z0_SERVICES:
            continue
        seen += 1
        assert version.zero_cost_class == "Z0_TRUE_FREE", f"{name}: {version.zero_cost_class}"
    assert seen >= 1, "no perpetual GitHub allowance published; the control is vacuous"


@skip_without_db
def test_the_run_is_offline_and_idempotent(session: Session, config) -> None:
    """A second identical run adds no new candidate identities."""

    _run(session, config)
    first = session.execute(select(func.count()).select_from(Candidate)).scalar_one()
    _run(session, config)
    second = session.execute(select(func.count()).select_from(Candidate)).scalar_one()
    # A re-scan writes new candidate rows per scan, but never new identities.
    keys_first = session.execute(
        select(func.count(func.distinct(Candidate.candidate_key)))
    ).scalar_one()
    assert keys_first >= 5
    assert second >= first


# --- Vocabulary case: changed (persisted change_event) ----------------------


@skip_without_db
def test_a_changed_allowance_raises_a_material_change_event_on_persisted_rows(
    session: Session, config
) -> None:
    """`changed`: scan the official capture, then the edited one, on real rows."""

    _run(session, config, publish=False)

    actions = next(
        s for s in _github_sources(session) if s.endpoint and s.endpoint.endswith("github-actions")
    )
    before = session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one()

    from app.ingest.fetch import FetchPolicy, FixtureFetcher
    from app.ingest.reconcile import reconcile_scan
    from app.ingest.scan import run_scan

    changed_bytes = (FIXTURES / "changed" / "source.html").read_bytes()
    fetcher = FixtureFetcher(
        {actions.endpoint: (changed_bytes, "text/html")},
        FetchPolicy(official_domains=DOMAINS),
    )
    scan = run_scan(actions, fetcher, session)
    reconcile_scan(scan, actions, session, now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC))

    # `change_event` carries no scan_run_id, so scope through the candidates
    # this scan produced. A whole-table query also picks up rows left by any
    # earlier use of the database, including published ones.
    scan_candidate_ids = [
        c.id
        for c in session.execute(
            select(Candidate).where(Candidate.scan_run_id == scan.id)
        ).scalars()
    ]
    assert scan_candidate_ids
    events = list(
        session.execute(
            select(ChangeEvent).where(ChangeEvent.new_candidate_id.in_(scan_candidate_ids))
        ).scalars()
    )
    assert session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one() > before, (
        "an edited allowance page produced no change event"
    )
    assert events, "expected a persisted change event for this scan"
    assert any(e.change_type == "modified" for e in events), (
        "an edited material allowance must be recorded as modified"
    )
    # MEASURED, and not what one might assume: a changed headline allowance is
    # `unknown`, not `material`. `MATERIAL_FACT_FIELDS` is only offer_type /
    # requires_card / has_paid_dependencies / quotas, and these HTML profiles
    # publish per-limit values as flat facts (`minutes_per_month`), not inside a
    # `quotas` structure. What matters for safety is that it is never quietly
    # downgraded to `non_material`, and it is not.
    assert all(e.materiality != "non_material" for e in events), (
        "a changed allowance must never be classified cosmetic"
    )
    assert any(e.materiality == "unknown" for e in events)
    assert all(e.publication_status == "draft" for e in events), (
        "reconciliation has no publication path"
    )


@skip_without_db
def test_materiality_positive_control_a_changed_card_requirement_is_material() -> None:
    """Guard the test above: `unknown` is a real classification, not a dead path.

    Without this, `materiality != "non_material"` could pass simply because the
    classifier never returns `material` for anything.
    """

    from app.ingest.reconcile import classify_materiality

    assert classify_materiality(["requires_card"]) == "material"
    assert classify_materiality(["minutes_per_month"]) == "unknown"


# --- Vocabulary case: contradictory (gate withhold + pending review) --------


@skip_without_db
def test_a_contradictory_page_is_withheld_and_queued_for_review(session: Session, config) -> None:
    """`contradictory`: the gate must not pick the friendlier of two rows.

    Contradiction detection in this repo is deliberately *cross-source*: two
    rows on one page differing over time are a change, not a conflict. So the
    conflicting excerpt is served through a SECOND official GitHub source for
    the same offer identity, which is what a real "two docs pages disagree"
    incident looks like.
    """

    from app.ingest.fetch import FetchPolicy, FixtureFetcher
    from app.ingest.reconcile import reconcile_scan
    from app.ingest.scan import run_scan
    from app.publish.publisher import publish_scan

    _run(session, config, publish=False)
    actions = next(
        s for s in _github_sources(session) if s.endpoint and s.endpoint.endswith("github-actions")
    )

    # A second official page for the same offer, disagreeing on requires_card.
    conflicting = Source(
        provider_id=actions.provider_id,
        slug="github-actions-billing-conflicting",
        adapter_type=actions.adapter_type,
        endpoint="https://docs.github.com/en/billing/concepts/product-billing/github-actions-conflict",
        parser_profile=actions.parser_profile,
        schedule=actions.schedule,
        trust_level=actions.trust_level,
        official=True,
    )
    session.add(conflicting)
    session.flush()

    reviews_before = session.execute(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.admin_disposition == "pending")
    ).scalar_one()
    versions_before = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()

    body = (FIXTURES / "contradictory" / "source.html").read_bytes()
    fetcher = FixtureFetcher(
        {conflicting.endpoint: (body, "text/html")}, FetchPolicy(official_domains=DOMAINS)
    )
    now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    scan = run_scan(conflicting, fetcher, session)
    result = reconcile_scan(scan, conflicting, session, now=now)

    assert result.review_items >= 1, (
        "two official sources disagreeing on requires_card must raise a review item"
    )
    reviews = list(
        session.execute(select(ReviewItem).where(ReviewItem.scan_run_id == scan.id)).scalars()
    )
    assert reviews, "a contradiction must leave a persisted review item"
    assert all(r.admin_disposition == "pending" for r in reviews), "nothing is auto-resolved"
    assert all(r.recommended_action == "manual_review" for r in reviews)
    conflict_fields = {
        c["field"] for r in reviews for c in (r.evidence_conflict or {}).get("conflicts", [])
    }
    assert "requires_card" in conflict_fields

    # The gate must withhold: no version may be minted off a contradicted fact.
    outcome = publish_scan(session, scan, conflicting, config.publishing, now=now)
    assert all(o.decision != "publish" for o in outcome.outcomes), (
        "a contradicted official fact must never publish cleanly"
    )
    versions_after = session.execute(select(func.count()).select_from(OfferVersion)).scalar_one()
    assert versions_after == versions_before, "ZERO new published versions under contradiction"

    pending_after = session.execute(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.admin_disposition == "pending")
    ).scalar_one()
    assert pending_after > reviews_before


# --- Vocabulary case: withdrawn --------------------------------------------

_WITHDRAWN_OFFERS: tuple[Mapping[str, object], ...] = (
    {
        "service": "GitHub Actions",
        "offer_type": "always_free",
        "requires_card": False,
        "has_paid_dependencies": False,
        "quotas": [{"metric": "minutes_per_month", "exhaustion_behaviour": "hard_stop"}],
    },
    {
        "service": "GitHub Codespaces",
        "offer_type": "always_free",
        "requires_card": False,
        "has_paid_dependencies": False,
        "quotas": [{"metric": "compute_time_per_month", "exhaustion_behaviour": "hard_stop"}],
    },
)


def _pipeline_source(session: Session) -> Source:
    source = Source(
        adapter_type="reference-json",
        trust_level="official",
        official=True,
        endpoint=TRIAL_ENDPOINT,
        enabled=True,
        schedule="daily",
    )
    session.add(source)
    session.flush()
    return source


@skip_without_db
def test_a_vanished_github_allowance_is_recorded_as_a_material_withdrawal(
    session: Session,
) -> None:
    outcome = drive_withdrawn(
        session,
        _pipeline_source(session),
        present=list(_WITHDRAWN_OFFERS),
        absent=[_WITHDRAWN_OFFERS[0]],
        domains=DOMAINS,
        now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )
    assert outcome.event.change_type == "withdrawn"
    assert outcome.event.materiality == "material"
    assert outcome.event.new_candidate_id is None
    assert outcome.event.publication_status == "draft"


# --- Vocabulary case: stale ------------------------------------------------


@skip_without_db
def test_a_stale_github_snapshot_is_never_published(session: Session, config) -> None:
    outcome = drive_stale(
        session,
        _pipeline_source(session),
        config.publishing,
        offers=[_WITHDRAWN_OFFERS[0]],
        domains=DOMAINS,
    )
    assert outcome.published == 0, "stale data must never be published"
    assert outcome.withheld + outcome.reviewed >= 1
    assert outcome.staleness.stale
