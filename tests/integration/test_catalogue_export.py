"""Integration tests for the catalogue static export (F009-S2, slice 1).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. Each test runs
inside a rolled-back transaction that publishes the real Cloudflare catalogue,
then drives :func:`app.export.catalogue.build_catalogue_export` against the same
committed+in-transaction state the live routes read.

The populated developer database this may run against holds an unrelated corpus
from earlier ingest work, so every assertion is scoped to data this module
controls (the Cloudflare offer it publishes, or a source it sets) rather than to
absolute catalogue totals.

Coverage maps to the mandatory invariants:

* Route parity (anti-drift): the exported ``data`` for all ten routes equals the
  live handler's output at the same clock -- the exporter cannot become a tenth
  surface that drifts from the other nine.
* Invariant 2 (staleness encoded in the data): the same rows exported one second
  either side of an offer's real expiry boundary flip ``evidence_currency`` in
  the artefact itself.
* Invariant 3 (unknown stays unknown): an offer whose backing snapshot has a NULL
  ``fetched_at`` exports as unchecked / not current, never free.
* Invariant 4 (per-source windows): the manifest reports each source's window via
  the same ``parse_schedule_window`` the currency path uses, so distinct
  schedules yield distinct windows rather than one global figure.
* Invariant 5 (deterministic): two builds at the same ``as_of`` render to
  byte-identical bytes.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.export.catalogue import build_catalogue_export, render_export
from app.ingest.reconcile import parse_schedule_window, window_to_compact
from app.ingest.runner import build_fixture_fetcher, run_provider_scans
from app.models.domain import Evidence, Offer, Source
from app.read_api import queries
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

# The read_api package re-exports the APIRouter instance as ``app.read_api.router``,
# which shadows the submodule for ``import ... as``. Reach the real module (whose
# ``_now`` seam the parity test pins) explicitly.
router = importlib.import_module("app.read_api.router")

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ingest" / "cloudflare" / "html"
FREE = "Z0_TRUE_FREE"

#: A fixed, injected clock so the export and the live handlers read the same now.
AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

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


def _publish(session: Session) -> None:
    model = load_and_validate(str(CONFIG_PATH))
    config = model if isinstance(model, ProviderConfig) else ProviderConfig(**model)
    fetcher = build_fixture_fetcher(config, FIXTURES_DIR)
    run_provider_scans(session, config, fetcher, publish=True)
    session.flush()
    provider = queries.fetch_provider(session, "cloudflare")
    assert provider is not None, "cloudflare provider should exist after publish"


def _free_offer(session: Session):
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


def _pin_clock(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    """Read the live handlers against the same injected clock as the export."""

    monkeypatch.setattr(router, "_now", lambda: now)


def _data(artefacts: dict, path: str):
    return artefacts[path]["data"]


# --------------------------------------------------------------------------- #
# Route parity (anti-drift) for all ten routes                                #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_export_matches_live_handlers_for_all_ten_routes(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(session)
    _pin_clock(monkeypatch, AS_OF)

    offer = _free_offer(session)
    offer_id = offer.id
    slug = offer.service.provider.slug

    artefacts = build_catalogue_export(session, as_of=AS_OF)

    # 1. providers list
    assert _data(artefacts, "catalogue/providers.json") == [
        p.model_dump(mode="json") for p in router.list_providers(session)
    ]
    # 2. provider detail
    assert _data(artefacts, f"catalogue/providers/{slug}.json") == router.get_provider(
        slug, session
    ).model_dump(mode="json")
    # 3. category-states
    assert _data(
        artefacts, f"catalogue/providers/{slug}/category-states.json"
    ) == router.get_category_states(slug, session).model_dump(mode="json")
    # 4. provider offers
    assert _data(artefacts, f"catalogue/providers/{slug}/offers.json") == [
        o.model_dump(mode="json") for o in router.list_provider_offers(slug, session)
    ]
    # 5. offer detail
    assert _data(artefacts, f"catalogue/offers/{offer_id}.json") == router.get_offer(
        offer_id, session
    ).model_dump(mode="json")
    # 6. offer evidence
    assert _data(
        artefacts, f"catalogue/offers/{offer_id}/evidence.json"
    ) == router.get_offer_evidence(offer_id, session).model_dump(mode="json")
    # 7. offer history
    assert _data(
        artefacts, f"catalogue/offers/{offer_id}/history.json"
    ) == router.get_offer_history(offer_id, session).model_dump(mode="json")
    # 8. categories
    assert _data(artefacts, "catalogue/categories.json") == router.get_category_matrix(
        session
    ).model_dump(mode="json")

    # 9. search: the corpus carries every live page-1 result verbatim (matched by
    #    offer id), proving the item shape and currency verdicts are identical.
    live_page = router.search_catalogue(session).model_dump(mode="json")["results"]
    corpus_by_id = {
        item["offer_id"]: item for item in _data(artefacts, "catalogue/search.json")["results"]
    }
    assert live_page, "expected at least one published offer in the live search page"
    for item in live_page:
        assert corpus_by_id[item["offer_id"]] == item

    # 10. compare: the corpus cell for the chosen offer equals the live compare
    #     cell for it (compared inside a real two-offer comparison).
    other_id = next(i for i in corpus_by_id if i != offer_id)
    live_compare = router.compare_offers(session, offers=f"{offer_id},{other_id}").model_dump(
        mode="json"
    )
    compare_cells = _data(artefacts, "catalogue/compare.json")["offers"]
    compare_corpus = {c["offer_id"]: c for c in compare_cells}
    for cell in live_compare["offers"]:
        assert compare_corpus[cell["offer_id"]] == cell


# --------------------------------------------------------------------------- #
# Invariant 2: staleness is encoded in the exported data (boundary flip)      #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_exported_offer_flips_current_across_its_real_expiry_boundary(session: Session) -> None:
    _publish(session)
    offer = _free_offer(session)
    version = queries.latest_version(offer)
    assert version is not None

    ages = [
        (e.snapshot.fetched_at, parse_schedule_window(e.source.schedule))
        for e in version.evidence
        if e.snapshot is not None and e.snapshot.fetched_at is not None
    ]
    assert ages, "the offer must rest on evidence with a real fetch time"
    fetched, window = min(ages, key=lambda pair: pair[0] + pair[1])

    # age == window -> NOT stale (the inequality is strict); +1s -> stale.
    at_boundary = build_catalogue_export(session, as_of=fetched + window)
    past_boundary = build_catalogue_export(session, as_of=fetched + window + timedelta(seconds=1))

    fresh = _data(at_boundary, f"catalogue/offers/{offer.id}.json")["evidence_currency"]
    stale = _data(past_boundary, f"catalogue/offers/{offer.id}.json")["evidence_currency"]

    # The ONLY difference between the two builds is one second of as_of, yet the
    # exported data changes: staleness is baked into the artefact, not derived at
    # render time.
    assert fresh["current"] is True
    assert stale["current"] is False
    assert stale["checked"] is True and stale["stale"] is True
    # And the collapsed presentation travels with it: an expired claim's label is
    # capped to "unknown" in the exported data. (Its freshness is a measured 0.0 --
    # a real zero, distinct from the NULL an UNCHECKED claim carries, which the
    # unknown-stays-unknown test asserts separately.)
    fresh_detail = _data(at_boundary, f"catalogue/offers/{offer.id}.json")
    stale_detail = _data(past_boundary, f"catalogue/offers/{offer.id}.json")
    assert stale_detail["confidence_label"] == "unknown"
    assert stale_detail["freshness"] == 0.0
    assert fresh_detail["confidence_label"] != "unknown"


# --------------------------------------------------------------------------- #
# Invariant 3: unknown stays unknown (NULL fetched_at)                        #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_exported_offer_with_uncheckable_evidence_is_not_current(session: Session) -> None:
    _publish(session)
    offer = _free_offer(session)
    version = queries.latest_version(offer)
    assert version is not None

    # Positive control: with its evidence anchored the offer is current shortly
    # after its evidence was fetched.
    now = max(e.snapshot.fetched_at for e in version.evidence if e.snapshot) + timedelta(seconds=1)
    before = _data(build_catalogue_export(session, as_of=now), f"catalogue/offers/{offer.id}.json")
    assert before["evidence_currency"]["current"] is True

    # Reach the "we could not look at all" arm the way real data can (the schema
    # forbids a NULL snapshot.fetched_at -- see test_catalogue_currency's
    # mechanism-H note): detach the evidence from the version so the anchor is
    # absent from the currency index entirely -> UNCHECKED.
    session.execute(
        update(Evidence)
        .where(Evidence.offer_version_id == version.id)
        .values(offer_version_id=None)
    )
    session.flush()
    session.expire_all()

    after = _data(build_catalogue_export(session, as_of=now), f"catalogue/offers/{offer.id}.json")
    currency = after["evidence_currency"]
    assert currency["checked"] is False
    assert currency["current"] is False
    assert currency["stale"] is False  # absence of evidence is not expiry
    assert after["freshness"] is None  # absent measurement, never 0.0 / free
    assert after["confidence_label"] == "unknown"


# --------------------------------------------------------------------------- #
# Invariant 4: per-source staleness windows (not one global figure)           #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_manifest_reports_per_source_windows_reusing_the_currency_derivation(
    session: Session,
) -> None:
    _publish(session)

    # Every source's manifest window is exactly what the currency path derives
    # from that source's own schedule -- the same function, not a second one.
    sources = session.execute(select(Source)).scalars().all()
    assert sources, "expected at least one configured source"
    expected = {s.slug: window_to_compact(parse_schedule_window(s.schedule)) for s in sources}

    manifest = build_catalogue_export(session, as_of=AS_OF)["manifest.json"]
    reported = {entry["source"]: entry["window"] for entry in manifest["staleness_windows"]}
    for slug, window in expected.items():
        assert reported[slug] == window


@skip_without_db
def test_manifest_windows_differ_per_source_not_one_global_figure(session: Session) -> None:
    _publish(session)

    # Deterministically create two sources with genuinely different cadences and
    # prove the manifest reports two DIFFERENT windows. A single global figure
    # would collapse these to one value and fail here.
    sources = session.execute(select(Source).order_by(Source.id)).scalars().all()
    assert len(sources) >= 2, "need at least two sources to demonstrate per-source windows"
    session.execute(update(Source).where(Source.id == sources[0].id).values(schedule="2d"))
    session.execute(update(Source).where(Source.id == sources[1].id).values(schedule="2h"))
    session.flush()
    session.expire_all()

    manifest = build_catalogue_export(session, as_of=AS_OF)["manifest.json"]
    windows = {entry["window"] for entry in manifest["staleness_windows"]}
    # Both distinct windows are present: the export derives a window per source,
    # so it cannot collapse to one global figure (which would fail here).
    assert "2d" in windows
    assert "2h" in windows
    assert len({"2d", "2h"} & windows) == 2


# --------------------------------------------------------------------------- #
# Invariant 5 + enumeration positive controls                                 #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_export_is_byte_identical_across_two_builds_at_same_as_of(session: Session) -> None:
    _publish(session)
    first = render_export(build_catalogue_export(session, as_of=AS_OF))
    second = render_export(build_catalogue_export(session, as_of=AS_OF))
    assert first == second


@skip_without_db
def test_manifest_counts_are_a_positive_control_for_enumeration(session: Session) -> None:
    _publish(session)
    artefacts = build_catalogue_export(session, as_of=AS_OF)
    manifest = artefacts["manifest.json"]

    # Cloudflare must be enumerated (a broken enumerator returns an empty list,
    # which this catches rather than mistaking for "nothing to export").
    assert "cloudflare" in manifest["providers"]
    assert manifest["counts"]["providers"] == len(manifest["providers"])
    assert manifest["counts"]["published_offers"] == len(manifest["offers"])
    assert manifest["counts"]["providers"] > 0
    assert manifest["counts"]["published_offers"] > 0

    # Every enumerated provider and offer produced its artefacts.
    for slug in manifest["providers"]:
        assert f"catalogue/providers/{slug}.json" in artefacts
        assert f"catalogue/providers/{slug}/category-states.json" in artefacts
        assert f"catalogue/providers/{slug}/offers.json" in artefacts
    for offer_id in manifest["offers"]:
        assert f"catalogue/offers/{offer_id}.json" in artefacts
        assert f"catalogue/offers/{offer_id}/evidence.json" in artefacts
        assert f"catalogue/offers/{offer_id}/history.json" in artefacts

    # The manifest's own artifact list agrees with what was built.
    assert set(manifest["artifacts"]) == set(artefacts.keys())


# --------------------------------------------------------------------------- #
# Invariant 3 (direction that silently publishes): an UNPUBLISHED offer must  #
# be absent from every artefact, including the search and compare corpora.    #
# --------------------------------------------------------------------------- #


@skip_without_db
def test_unpublished_offer_is_absent_from_every_artefact(session: Session) -> None:
    _publish(session)
    published = _free_offer(session)
    published_id = published.id
    service = published.service
    slug = service.provider.slug

    def _corpus_ids(artefacts: dict) -> dict[str, set[int]]:
        search = _data(artefacts, "catalogue/search.json")["results"]
        compare = _data(artefacts, "catalogue/compare.json")["offers"]
        provider = _data(artefacts, f"catalogue/providers/{slug}/offers.json")
        return {
            "search": {r["offer_id"] for r in search},
            "compare": {c["offer_id"] for c in compare},
            "provider": {o["offer_id"] for o in provider},
        }

    # Positive control: the published, evidence-backed offer IS present on every
    # surface an unpublished one must never reach. Without this, absence below
    # could be a stuck instrument (present nowhere because the surfaces are empty).
    before = build_catalogue_export(session, as_of=AS_OF)
    assert f"catalogue/offers/{published_id}.json" in before
    assert published_id in before["manifest.json"]["offers"]
    ids_before = _corpus_ids(before)
    assert published_id in ids_before["search"]
    assert published_id in ids_before["compare"]
    assert published_id in ids_before["provider"]

    # An offer with no immutable version is unpublished (queries.is_published),
    # the state a static host must never render as a live free claim.
    unpublished = Offer(
        service_id=service.id,
        offer_type=published.offer_type,
        zero_cost_class=FREE,
    )
    session.add(unpublished)
    session.flush()
    # Force the exporter to genuinely re-traverse the offer graph from the DB,
    # so its absence below is the is_published gate rejecting a loaded offer,
    # not a stale in-memory collection that never contained it.
    session.expire_all()
    assert queries.is_published(unpublished) is False
    unpublished_id = unpublished.id
    assert unpublished_id != published_id

    after = build_catalogue_export(session, as_of=AS_OF)

    # It has no resource artefact, is not enumerated in the manifest, and — the
    # direction that silently publishes — is in neither the search nor compare
    # corpus that slice 2 queries client-side.
    assert f"catalogue/offers/{unpublished_id}.json" not in after
    assert f"catalogue/offers/{unpublished_id}/evidence.json" not in after
    assert f"catalogue/offers/{unpublished_id}/history.json" not in after
    assert unpublished_id not in after["manifest.json"]["offers"]
    ids_after = _corpus_ids(after)
    assert unpublished_id not in ids_after["search"]
    assert unpublished_id not in ids_after["compare"]
    assert unpublished_id not in ids_after["provider"]
    # Positive control still holds: the published sibling is still everywhere.
    assert published_id in ids_after["search"]
    assert published_id in ids_after["compare"]
    assert published_id in ids_after["provider"]
