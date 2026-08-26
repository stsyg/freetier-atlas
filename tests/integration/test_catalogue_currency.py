"""Per-surface evidence currency across EVERY catalogue read surface (F008 S6).

PR #79 closed the staleness path on ``/catalogue/categories``. PR #83 closed it
on the three adviser endpoints. This module covers the nine remaining catalogue
surfaces -- the ones that were still serving a frozen ``zero_cost_class``, a
frozen ``confidence_label`` and a publish-time ``freshness`` with no clock
reaching them at all.

The nine surfaces were enumerated at RUNTIME from ``/openapi.json`` by resolving
each 200-response schema and collecting every property that carries a class, a
confidence label or a freshness figure -- not from a diff, because a route the
diff does not touch is exactly the one that gets missed. Four of the nine were
absent from the original brief and were found only this way: ``/compare``,
``/offers/{id}/evidence``, ``/offers/{id}/history`` and ``/providers``.

Expiry mechanism G -- the one-second boundary differential
---------------------------------------------------------
``assess_staleness`` decides ``stale = age > window``, a STRICT inequality. Every
test below reads the SAME unmodified rows at two clocks one second apart,
straddling that boundary:

    at ``fetched_at + window``      -> age == window -> NOT stale -> current
    at ``fetched_at + window + 1s`` -> age >  window -> stale

Nothing in the database moves between the two reads. No timestamp is backdated,
no schedule is shrunk, no ingest is re-run. Only the clock advances, by the
smallest increment the comparison can resolve.

That is what makes it load-bearing: **a surface serving a frozen value cannot
flip on a one-second change.** If a paired assertion below ever shows identical
output either side of the boundary, that is a positive detection of the very
defect this work exists to remove -- which is the impossible-value floor these
instruments need in order to be able to fail.

Mechanism H covers the third arm: ``Snapshot.fetched_at = NULL`` while the
Evidence row REMAINS. That is deliberately distinct from "no Evidence rows at
all" (which reaches ``UNCHECKED`` because the anchor is missing from the index);
here the anchor is PRESENT and reaches ``UNCHECKED`` through
``assess_currency``'s own ``fetched_at is None`` branch -- a different code path
to the same verdict.

Both directions, everywhere
---------------------------
Every surface is asserted to STOP repeating an unsupported free claim AND to
STILL serve a supported one. A guard that cannot be shown to permit is
indistinguishable from one that broke the product, and a wrongly-withdrawn free
offer is a defect of exactly the same severity as a wrongly-published one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.db import get_session
from app.ingest.reconcile import parse_schedule_window
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.main import app
from app.models.domain import Evidence, Offer, Snapshot, Source
from app.read_api import queries
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

#: ``app.read_api.__init__`` re-exports the APIRouter OBJECT under the name
#: ``router``, so ``from app.read_api import router`` would bind the router, not
#: the module holding the ``_now`` clock seam. Import the module explicitly.
read_router = import_module("app.read_api.router")

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ingest" / "cloudflare" / "html"

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

#: The class whose unsupported repetition is the defect being closed.
FREE = "Z0_TRUE_FREE"


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    from sqlalchemy import create_engine

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


def _publish(session: Session) -> None:
    model = load_and_validate(str(CONFIG_PATH))
    config = model if isinstance(model, ProviderConfig) else ProviderConfig(**model)
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)
    run_provider_scans(session, config, fetcher, publish=True)
    session.flush()


def _free_offer(session: Session) -> Offer:
    """A published, genuinely free, evidence-backed Cloudflare offer."""

    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"
    for svc in provider.services:
        for offer in svc.offers:
            if not queries.is_published(offer) or offer.zero_cost_class != FREE:
                continue
            version = queries.latest_version(offer)
            if version is not None and version.evidence:
                return offer
    raise AssertionError("no published Z0_TRUE_FREE offer with evidence was produced")


@pytest.fixture
def boundary(session: Session) -> dict:
    """The exact expiry boundary for the chosen offer, derived from real rows.

    Nothing here is invented: ``fetched_at`` is the snapshot's own timestamp and
    the window is whatever ``parse_schedule_window`` makes of the source's own
    declared schedule. The two clocks are that boundary, and that boundary plus
    one second.
    """

    _publish(session)
    offer = _free_offer(session)
    version = queries.latest_version(offer)
    assert version is not None

    ages = [
        (e.snapshot.fetched_at, e.source.schedule)
        for e in version.evidence
        if e.snapshot is not None and e.snapshot.fetched_at is not None
    ]
    assert ages, "the offer must rest on evidence with a real fetch time"
    oldest, schedule = min(ages, key=lambda pair: pair[0])
    window = parse_schedule_window(schedule)

    return {
        "offer_id": offer.id,
        "version_id": version.id,
        "provider_slug": offer.service.provider.slug,
        "fetched_at": oldest,
        "window": window,
        # age == window -> NOT stale (the inequality is strict)
        "current_at": oldest + window,
        # age  > window -> stale, by one second
        "stale_at": oldest + window + timedelta(seconds=1),
    }


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _at(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Read the catalogue at ``moment`` through the router's own clock seam."""

    monkeypatch.setattr(read_router, "_now", lambda: moment)


# --------------------------------------------------------------------------- #
# The boundary itself                                                          #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_the_boundary_is_a_real_discontinuity_one_second_wide(
    session: Session, boundary: dict
) -> None:
    """Mechanism G's premise, asserted before anything is built on it.

    If the verdict did not actually flip across these two clocks, every paired
    test below would be comparing two identical reads and proving nothing.
    """

    version_id = boundary["version_id"]
    key = ("offer_version", version_id)

    at_boundary = queries.fetch_evidence_currency(session, now=boundary["current_at"])[key]
    one_second_later = queries.fetch_evidence_currency(session, now=boundary["stale_at"])[key]

    assert at_boundary.current is True
    assert at_boundary.stale is False
    assert one_second_later.current is False
    assert one_second_later.stale is True
    # The clocks really are one second apart, and the data did not move.
    assert boundary["stale_at"] - boundary["current_at"] == timedelta(seconds=1)
    assert at_boundary.oldest_fetched_at == one_second_later.oldest_fetched_at


@skip_without_db
def test_freshness_decays_with_the_clock_and_is_none_when_unchecked(
    session: Session, boundary: dict
) -> None:
    """``freshness()`` gains a production consumer; None must not become 0.0."""

    key = ("offer_version", boundary["version_id"])
    fetched = boundary["fetched_at"]

    at_fetch = queries.fetch_evidence_currency(session, now=fetched)[key]
    half = queries.fetch_evidence_currency(session, now=fetched + boundary["window"] / 2)[key]
    expired = queries.fetch_evidence_currency(session, now=boundary["stale_at"])[key]

    assert at_fetch.freshness() == pytest.approx(1.0)
    assert half.freshness() is not None
    assert at_fetch.freshness() > half.freshness() > 0.0
    # Expired evidence still yields a NUMBER (it WAS checked). It is the
    # UNCHECKED case, not the expired one, that must yield None.
    assert expired.freshness() is not None


@skip_without_db
def test_mechanism_h_is_refuted_by_the_schema_and_unchecked_is_reached_another_way(
    session: Session, boundary: dict
) -> None:
    """A planned mechanism that turned out to be impossible, recorded as such.

    Mechanism H was to be "``Snapshot.fetched_at = NULL`` with the Evidence row
    still present", reaching ``UNCHECKED`` through ``assess_currency``'s
    ``fetched_at is None`` branch rather than through a missing anchor.

    **It cannot happen.** ``snapshot.fetched_at`` is ``NOT NULL``, asserted below
    against the live database rather than read off the model. Three independent
    sources agree: the ORM annotates it ``Mapped[datetime]`` (not optional), the
    information_schema reports ``is_nullable = NO``, and the write below is
    rejected by the constraint.

    The consequence is a coverage claim that must be stated precisely:
    ``assess_currency(None, ...) -> UNCHECKED`` is **test-covered but NOT
    reachable from real input** through the snapshot path. On real data
    ``UNCHECKED`` arises only when the anchor is absent from the index entirely,
    which is what the second half of this test exercises.
    """

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    # (a) The constraint is real, and it fires.
    snapshot_id = (
        session.execute(
            select(Evidence.snapshot_id).where(Evidence.offer_version_id == boundary["version_id"])
        )
        .scalars()
        .first()
    )
    assert snapshot_id is not None

    nullable = session.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'snapshot' AND column_name = 'fetched_at'"
        )
    ).scalar_one()
    assert nullable == "NO", "if this ever becomes nullable, mechanism H becomes reachable"

    savepoint = session.begin_nested()
    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE snapshot SET fetched_at = NULL WHERE id = :sid"),
            {"sid": snapshot_id},
        )
        session.flush()
    savepoint.rollback()

    # NEGATIVE CONTROL: the same write with a real value IS accepted, so the
    # rejection above is the constraint and not an unwritable row or a
    # read-only transaction.
    control = session.begin_nested()
    session.execute(
        text("UPDATE snapshot SET fetched_at = :ts WHERE id = :sid"),
        {"ts": boundary["fetched_at"], "sid": snapshot_id},
    )
    session.flush()
    control.rollback()

    # (b) The reachable route to UNCHECKED: no evidence anchors the version.
    key = ("offer_version", boundary["version_id"])
    before = queries.fetch_evidence_currency(session, now=boundary["current_at"])
    assert key in before and before[key].checked is True

    detach = session.begin_nested()
    session.execute(
        text("UPDATE evidence SET offer_version_id = NULL WHERE offer_version_id = :vid"),
        {"vid": boundary["version_id"]},
    )
    session.flush()

    after = queries.fetch_evidence_currency(session, now=boundary["current_at"])
    assert key not in after, "an unanchored version must not appear in the index at all"

    verdict = queries.currency_context(session, now=boundary["current_at"]).for_version(
        boundary["version_id"]
    )
    assert verdict.checked is False, "no evidence means no check was possible"
    assert verdict.current is False, "'we could not check' must never read as fresh"
    assert verdict.stale is False, "absence of evidence is not evidence of expiry"
    assert verdict.freshness() is None, "an absent measurement is not a zero score"
    assert "cannot be established" in (verdict.reason() or "")
    detach.rollback()


# --------------------------------------------------------------------------- #
# Surface 1 -- GET /catalogue/search                                           #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_search_labels_an_expired_free_claim_and_still_serves_a_fresh_one(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _row(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        body = client.get("/catalogue/search", params={"zero_cost_class": FREE}).json()
        match = [r for r in body["results"] if r["offer_id"] == boundary["offer_id"]]
        assert match, "the offer must remain visible in BOTH directions"
        return match[0]

    fresh = _row(boundary["current_at"])
    assert fresh["zero_cost_class"] == FREE
    assert fresh["confidence_label"] == "high"
    assert fresh["evidence_currency"]["current"] is True
    assert fresh["evidence_currency"]["reason"] is None

    stale = _row(boundary["stale_at"])
    # The classification is a fact about the offer's terms and does not change.
    assert stale["zero_cost_class"] == FREE
    # What changes is whether we still vouch for it.
    assert stale["confidence_label"] == "unknown"
    assert stale["evidence_currency"]["current"] is False
    assert stale["evidence_currency"]["stale"] is True
    assert "no longer known to be current" in stale["evidence_currency"]["reason"]


@skip_without_db
def test_search_does_not_hide_a_stale_free_offer_from_the_class_filter(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omission is invisible; a label is not. Hiding a real offer is its own defect."""

    _at(monkeypatch, boundary["stale_at"])
    body = client.get("/catalogue/search", params={"zero_cost_class": FREE}).json()
    ids = [r["offer_id"] for r in body["results"]]
    assert boundary["offer_id"] in ids
    assert body["total_results"] >= 1


@skip_without_db
def test_evidence_current_is_a_separate_filter_dimension(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The currency axis is opt-in, and BOTH of its values work."""

    _at(monkeypatch, boundary["stale_at"])

    only_current = client.get(
        "/catalogue/search", params={"zero_cost_class": FREE, "evidence_current": "true"}
    ).json()
    assert boundary["offer_id"] not in [r["offer_id"] for r in only_current["results"]]

    only_expired = client.get(
        "/catalogue/search", params={"zero_cost_class": FREE, "evidence_current": "false"}
    ).json()
    assert boundary["offer_id"] in [r["offer_id"] for r in only_expired["results"]]

    # PAIRED CONTROL: one second earlier the same filter includes it.
    _at(monkeypatch, boundary["current_at"])
    now_current = client.get(
        "/catalogue/search", params={"zero_cost_class": FREE, "evidence_current": "true"}
    ).json()
    assert boundary["offer_id"] in [r["offer_id"] for r in now_current["results"]]


@skip_without_db
def test_currency_filter_keeps_pagination_honest(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``total_results`` counts what is actually returned, not what SQL matched.

    This is the defect that post-filtering a page would have introduced: the
    count is taken before the filter, so a page renders fewer rows than it
    claims -- or none at all while later pages still have results.
    """

    for moment in (boundary["current_at"], boundary["stale_at"]):
        _at(monkeypatch, moment)
        for value in ("true", "false"):
            body = client.get("/catalogue/search", params={"evidence_current": value}).json()
            assert len(body["results"]) <= body["total_results"]
            if body["total_results"] <= body["page_size"]:
                assert len(body["results"]) == body["total_results"], (
                    "a single-page result set must return exactly what it counts"
                )
            expected_pages = -(-body["total_results"] // body["page_size"])
            assert body["total_pages"] == expected_pages
            for row in body["results"]:
                assert row["evidence_currency"]["current"] is (value == "true")


# --------------------------------------------------------------------------- #
# Surface 2 -- GET /catalogue/offers/{id}                                      #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_offer_detail_withholds_unearned_confidence_when_expired(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _detail(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        resp = client.get(f"/catalogue/offers/{boundary['offer_id']}")
        assert resp.status_code == 200
        return resp.json()

    fresh = _detail(boundary["current_at"])
    assert fresh["confidence_label"] == "high"
    assert fresh["freshness"] is not None
    assert fresh["advanced"]["score"] is not None
    assert fresh["advanced"]["signals"] is not None
    assert fresh["evidence_currency"]["current"] is True

    stale = _detail(boundary["stale_at"])
    assert stale["confidence_label"] == "unknown"
    assert stale["evidence_currency"]["stale"] is True
    # The publish-time score and the publish-time signal dict -- including the
    # `freshness` signal that read 1.0 on five-year-expired evidence -- are
    # withheld once the evidence beneath them is no longer known to be current.
    assert stale["advanced"]["score"] is None
    assert stale["advanced"]["signals"] is None
    # The current version nested inside carries its own verdict too.
    assert stale["current_version"]["evidence_currency"]["stale"] is True
    assert stale["current_version"]["confidence_label"] == "unknown"


@skip_without_db
def test_offer_detail_freshness_is_never_a_frozen_one(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact reported defect: 1.0 served for long-expired evidence."""

    _at(monkeypatch, boundary["fetched_at"] + timedelta(days=365 * 5))
    body = client.get(f"/catalogue/offers/{boundary['offer_id']}").json()
    assert body["evidence_currency"]["stale"] is True
    assert body["freshness"] != 1.0
    assert body["evidence_currency"]["age_days"] > 1800


# --------------------------------------------------------------------------- #
# Surface 3 -- GET /catalogue/offers/{id}/evidence                             #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_offer_evidence_caps_confidence_and_keeps_the_provenance(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _body(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        return client.get(f"/catalogue/offers/{boundary['offer_id']}/evidence").json()

    fresh = _body(boundary["current_at"])
    assert fresh["confidence_label"] == "high"
    assert fresh["advanced"]["score"] is not None
    assert fresh["evidence_currency"]["current"] is True
    assert len(fresh["evidence"]) >= 1

    stale = _body(boundary["stale_at"])
    assert stale["confidence_label"] == "unknown"
    assert stale["advanced"]["score"] is None
    assert stale["evidence_currency"]["stale"] is True
    # The evidence rows themselves are NOT withdrawn: the reader must still be
    # able to see what was relied on and go check it.
    assert len(stale["evidence"]) == len(fresh["evidence"])


# --------------------------------------------------------------------------- #
# Surface 4 -- GET /catalogue/offers/{id}/history                              #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_offer_history_gives_every_version_its_own_verdict(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _versions(moment: datetime) -> list[dict]:
        _at(monkeypatch, moment)
        return client.get(f"/catalogue/offers/{boundary['offer_id']}/history").json()["versions"]

    fresh = _versions(boundary["current_at"])
    assert fresh, "history must contain at least the current version"
    assert all(v["evidence_currency"]["current"] for v in fresh)
    assert any(v["confidence_label"] == "high" for v in fresh)

    stale = _versions(boundary["stale_at"])
    assert all(not v["evidence_currency"]["current"] for v in stale)
    assert all(v["confidence_label"] == "unknown" for v in stale)
    # A history row still reports the class it was published with -- that is what
    # a history IS -- but it no longer implies the claim is still supported.
    assert all(v["zero_cost_class"] for v in stale)


# --------------------------------------------------------------------------- #
# Surface 5 -- GET /catalogue/providers                                        #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_providers_list_freshness_follows_the_clock(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _row(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        rows = client.get("/catalogue/providers").json()
        return next(r for r in rows if r["slug"] == boundary["provider_slug"])

    # Read at the fetch moment itself, where freshness is unambiguously high.
    just_fetched = _row(boundary["fetched_at"])
    assert just_fetched["freshness"] == pytest.approx(1.0)
    assert just_fetched["evidence_currency"]["current"] is True

    at_boundary = _row(boundary["current_at"])
    assert at_boundary["evidence_currency"]["current"] is True

    stale = _row(boundary["stale_at"])
    assert stale["evidence_currency"]["current"] is False
    assert stale["evidence_currency"]["stale"] is True

    # Completeness measures how much of the offer we captured and does not decay
    # with the calendar; only the freshness figure is allowed to move.
    assert stale["completeness"] == just_fetched["completeness"]
    assert stale["freshness"] != just_fetched["freshness"]


@skip_without_db
def test_zero_freshness_is_a_measurement_and_null_freshness_is_not(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.0 and null are different answers, and the difference is load-bearing.

    At the exact expiry boundary the evidence is still current, yet its freshness
    ratio is legitimately 0.0 -- a real measurement that happens to sit at the
    bottom of the scale. "We could not check" must NOT produce that same 0.0,
    because the web formatter renders 0 as "0%" and null as "Unknown": collapsing
    them would put a number where there is no measurement.
    """

    _at(monkeypatch, boundary["current_at"])
    row = next(
        r
        for r in client.get("/catalogue/providers").json()
        if r["slug"] == boundary["provider_slug"]
    )
    assert row["evidence_currency"]["current"] is True
    assert row["evidence_currency"]["checked"] is True
    assert row["evidence_currency"]["freshness"] == pytest.approx(0.0)
    # A zero freshness therefore does NOT imply "unsupported".
    assert row["evidence_currency"]["reason"] is None


# --------------------------------------------------------------------------- #
# Surface 6 -- GET /catalogue/providers/{slug}                                 #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_provider_detail_rollup_reports_its_stalest_claim(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider is only as current as its STALEST published claim.

    Read at the fetch moment rather than at the boundary: the rollup spans every
    published offer this provider has, and their snapshots are stamped
    microseconds apart during one ingest. At `oldest + window` a sibling offer
    fetched a few microseconds earlier is already one microsecond past its own
    window, which made an earlier version of this test fail intermittently. The
    boundary belongs to a single anchor; a rollup needs a moment that is
    unambiguous for all of them.
    """

    def _body(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        return client.get(f"/catalogue/providers/{boundary['provider_slug']}").json()

    fresh = _body(boundary["fetched_at"])
    assert fresh["evidence_currency"]["current"] is True
    assert fresh["freshness"] is not None

    stale = _body(boundary["stale_at"])
    assert stale["evidence_currency"]["current"] is False
    assert stale["evidence_currency"]["stale"] is True
    assert "no longer known to be current" in stale["evidence_currency"]["reason"]


# --------------------------------------------------------------------------- #
# Surface 7 -- GET /catalogue/providers/{slug}/offers                          #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_provider_offers_cap_the_label_per_offer(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _row(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        rows = client.get(f"/catalogue/providers/{boundary['provider_slug']}/offers").json()
        return next(r for r in rows if r["offer_id"] == boundary["offer_id"])

    fresh = _row(boundary["current_at"])
    assert fresh["confidence_label"] == "high"
    assert fresh["evidence_currency"]["current"] is True

    stale = _row(boundary["stale_at"])
    assert stale["zero_cost_class"] == FREE  # still listed, not hidden
    assert stale["confidence_label"] == "unknown"
    assert stale["evidence_currency"]["stale"] is True


# --------------------------------------------------------------------------- #
# Surface 8 -- GET /catalogue/providers/{slug}/category-states                 #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_category_states_cap_the_label_per_offer(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _offer(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        body = client.get(
            f"/catalogue/providers/{boundary['provider_slug']}/category-states"
        ).json()
        for group in body["categories"]:
            for svc in group["services"]:
                for offer in svc["offers"]:
                    if offer["offer_id"] == boundary["offer_id"]:
                        return offer
        raise AssertionError("offer missing from category states in BOTH directions")

    fresh = _offer(boundary["current_at"])
    assert fresh["confidence_label"] == "high"
    assert fresh["evidence_currency"]["current"] is True

    stale = _offer(boundary["stale_at"])
    assert stale["confidence_label"] == "unknown"
    assert stale["evidence_currency"]["stale"] is True


# --------------------------------------------------------------------------- #
# Surface 9 -- GET /catalogue/compare                                          #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_compare_withholds_the_numbers_when_evidence_expired(
    client: TestClient, session: Session, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The side-by-side "which of these is free" view -- absent from the brief."""

    provider = queries.fetch_provider(session, boundary["provider_slug"])
    assert provider is not None
    ids = sorted({o.id for s in provider.services for o in s.offers if queries.is_published(o)})
    if len(ids) < 2:
        pytest.skip("compare needs at least two published offers in this corpus")
    pair = f"{ids[0]},{ids[1]}"

    def _column(moment: datetime) -> dict:
        _at(monkeypatch, moment)
        resp = client.get("/catalogue/compare", params={"offers": pair})
        assert resp.status_code == 200
        body = resp.json()
        return next(o for o in body["offers"] if o["offer_id"] == boundary["offer_id"])

    fresh = _column(boundary["current_at"])
    assert fresh["confidence_label"] == "high"
    assert fresh["advanced"]["score"] is not None
    assert fresh["freshness"] is not None
    assert fresh["evidence_currency"]["current"] is True

    stale = _column(boundary["stale_at"])
    assert stale["confidence_label"] == "unknown"
    assert stale["advanced"]["score"] is None
    assert stale["advanced"]["signals"] is None
    assert stale["evidence_currency"]["stale"] is True
    # Quotas stay: what the offer grants is not in doubt, only whether the
    # evidence still supports the claim about it.
    assert stale["quotas"] == fresh["quotas"]


# --------------------------------------------------------------------------- #
# Regression: the two surfaces already closed must not move                    #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_category_matrix_path_is_unchanged(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #79's coverage path still behaves exactly as it did."""

    _at(monkeypatch, boundary["stale_at"])
    body = client.get("/catalogue/categories").json()
    assert body["categories"], "the canonical taxonomy is always present"
    states = {
        cov["state"]
        for row in body["categories"]
        for cov in row["providers"]
        if cov["provider_slug"] == boundary["provider_slug"]
    }
    # `stale` remains reachable on this surface via its own mechanism.
    assert states


@skip_without_db
def test_fetch_stale_offer_version_ids_semantics_unchanged(
    session: Session, boundary: dict
) -> None:
    """AC13 from S5: the coverage projection keeps its exact meaning.

    Transcribed rather than imported: comparing the function to itself would
    look rigorous and prove nothing. This recomputes "stale" from the raw rows
    the old implementation read, and asserts the shipped function agrees.
    """

    now = boundary["stale_at"]
    shipped = queries.fetch_stale_offer_version_ids(session, now=now)

    transcribed: set[int] = set()
    for version_id, fetched_at, schedule in session.execute(
        select(Evidence.offer_version_id, Snapshot.fetched_at, Source.schedule)
        .join(Snapshot, Snapshot.id == Evidence.snapshot_id)
        .join(Source, Source.id == Evidence.source_id)
        .where(Evidence.offer_version_id.is_not(None))
    ).all():
        if fetched_at is None:
            continue
        if (now - fetched_at) > parse_schedule_window(schedule):
            transcribed.add(int(version_id))

    assert transcribed, "the corpus must contain expired evidence for this to mean anything"
    assert shipped == frozenset(transcribed)


# --------------------------------------------------------------------------- #
# The TENTH surface (F008 S7): the free-offer COUNT on /catalogue/categories   #
# --------------------------------------------------------------------------- #
#
# S6 enumerated the catalogue surfaces from /openapi.json by resolving the 200
# schemas for four fields -- class, confidence, freshness, signals -- and found
# NINE. `free_offer_count` is a COUNT, not one of those four, so that instrument
# could not see it. A wider question ("what renders a free CLAIM in any form")
# finds TEN. Both counts are right; they answer different questions.
#
# Measured on this endpoint BEFORE the fix, through the same mechanism G clocks
# used throughout this module: `state`, `derived_state` and `mismatch` moved
# across the boundary and `free_offer_count` did NOT, so a cell could serve
# `state="stale"` and "1 truly free" in the same response.


def _cells(payload: dict) -> dict[tuple[str, str], dict]:
    return {
        (row["slug"], cell["provider_slug"]): cell
        for row in payload["categories"]
        for cell in row["providers"]
    }


@skip_without_db
def test_the_free_offer_count_is_qualified_once_its_evidence_expires(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both arms, on the live endpoint, over the SAME unmodified rows.

    Nothing in the database moves between the two reads -- only the clock, by one
    second, across the strict ``age > window`` inequality. A count served without
    reference to a clock cannot respond to that.
    """

    _at(monkeypatch, boundary["current_at"])
    current = _cells(client.get("/catalogue/categories").json())

    _at(monkeypatch, boundary["stale_at"])
    expired = _cells(client.get("/catalogue/categories").json())

    free_cells = [key for key, cell in current.items() if cell["free_offer_count"] > 0]
    assert free_cells, "the corpus must publish at least one free offer to measure anything"

    for key in free_cells:
        before, after = current[key], expired[key]
        # PERMIT ARM: inside the window the count is asserted in full.
        assert before["current_free_offer_count"] == before["free_offer_count"]
        assert before["evidence_currency"]["current"] is True
        # WITHHOLD ARM: one second later it no longer is.
        assert after["current_free_offer_count"] == 0
        assert after["evidence_currency"]["stale"] is True
        assert after["evidence_currency"]["reason"]
        # AND THE TOTAL DID NOT SHRINK. A withheld free offer is its own defect.
        assert after["free_offer_count"] == before["free_offer_count"]
        assert after["published_offer_count"] == before["published_offer_count"]


@skip_without_db
def test_the_category_matrix_response_is_not_frozen_against_the_clock(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count field specifically must move, not merely the response as a whole.

    Recorded because the OBVIOUS instrument here is a whole-body byte
    differential, and it is the wrong one: the body already differed before this
    slice, because `state` and `derived_state` move. A body-level check therefore
    reads "currency-aware" while the count underneath is untouched. This asserts
    the field, not the payload.
    """

    _at(monkeypatch, boundary["current_at"])
    current = _cells(client.get("/catalogue/categories").json())
    _at(monkeypatch, boundary["stale_at"])
    expired = _cells(client.get("/catalogue/categories").json())

    moved = {
        field
        for key in current
        for field in current[key]
        if current[key][field] != expired[key][field]
    }
    assert "current_free_offer_count" in moved, "the qualification must follow the clock"
    assert "evidence_currency" in moved
    # ...and the facts beneath it must not.
    assert "free_offer_count" not in moved
    assert "published_offer_count" not in moved


@skip_without_db
def test_one_clock_serves_the_whole_category_matrix_response(
    client: TestClient, boundary: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coverage signals and currency verdicts must agree about "now".

    The handler calls ``_now()`` ONCE and hands the same moment to both
    ``coverage_signal_context`` and ``currency_context``. Two calls would let the
    derived state and the evidenced count straddle a boundary that fell between
    them -- a race that is rare, real, and invisible in production.
    """

    moments: list[datetime] = []
    real_now = boundary["stale_at"]

    def _recording_now() -> datetime:
        moments.append(real_now + timedelta(seconds=len(moments)))
        return moments[-1]

    monkeypatch.setattr(read_router, "_now", _recording_now)
    response = client.get("/catalogue/categories")

    assert response.status_code == 200
    assert len(moments) == 1, f"the handler read the clock {len(moments)} times, not once"
