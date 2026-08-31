"""Offline unit tests for the read-only catalogue API (F005 slice 3).

These tests never touch a live database. They exercise:

* the confidence label mapping (D039 boundaries + honest ``"unknown"``),
* the ORM -> schema serialization against an in-memory published graph, and
* the HTTP routes via ``TestClient`` with ``queries`` monkeypatched to return
  that in-memory graph -- asserting GET-only behaviour, 404s, slug validation
  (no fetchable-URL input), the label-primary / numeric-in-advanced rule, and
  that no community/candidate data is present.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db import get_session
from app.main import app
from app.models.domain import (
    Category,
    ChangeEvent,
    Evidence,
    Offer,
    OfferVersion,
    Provider,
    Quota,
    Service,
    Snapshot,
    Source,
)
from app.read_api import confidence, queries, service
from app.read_api.currency import ANCHOR_OFFER_VERSION, CurrencyContext, assess_currency
from fastapi.testclient import TestClient

#: Sentinel so ``fetched_at=None`` ("no fetch time at all") stays distinguishable
#: from "caller did not say", which defaults to a genuinely fresh timestamp.
_UNSET = object()


def _graph_currency(graph: dict, now: datetime | None = None) -> CurrencyContext:
    """Derive a real currency context FROM the graph's own evidence.

    Deliberately derived rather than hardcoded: if the fixture's snapshot is
    aged, these tests see "stale" without any further change. A hardcoded
    "current" context would make every assertion below vacuous.
    """

    moment = now or datetime.now(UTC)
    evidence = graph["evidence"]
    verdict = assess_currency(evidence.snapshot.fetched_at, moment, evidence.source.schedule)
    return CurrencyContext(index={(ANCHOR_OFFER_VERSION, graph["version"].id): verdict}, now=moment)


# --------------------------------------------------------------------------- #
# Confidence label mapping                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.95, "high"),
        (0.90, "high"),
        (0.80, "medium"),
        (0.70, "medium"),
        (0.50, "low"),
        (0.0, "low"),
    ],
)
def test_confidence_label_boundaries(score: float, expected: str) -> None:
    assert (
        confidence.confidence_label(score, automatic_threshold=0.90, uncertain_threshold=0.70)
        == expected
    )


def test_confidence_label_unknown_for_missing_score() -> None:
    assert confidence.confidence_label(None) == "unknown"


def test_confidence_label_unknown_for_nan() -> None:
    assert confidence.confidence_label(float("nan")) == "unknown"


def test_confidence_label_uses_default_thresholds() -> None:
    # 0.92 >= default automatic (0.90) -> high; 0.75 -> medium; 0.4 -> low.
    assert confidence.confidence_label(0.92) == "high"
    assert confidence.confidence_label(0.75) == "medium"
    assert confidence.confidence_label(0.40) == "low"


def test_confidence_label_handles_inverted_thresholds() -> None:
    # Degenerate pair must not raise and must stay deterministic.
    assert (
        confidence.confidence_label(0.8, automatic_threshold=0.5, uncertain_threshold=0.9)
        in confidence.CONFIDENCE_LABELS
    )


# --------------------------------------------------------------------------- #
# In-memory published graph                                                   #
# --------------------------------------------------------------------------- #


def _material_facts(confidence_score: float = 0.93) -> dict:
    return {
        "offer_type": "always_free",
        "requires_card": False,
        "has_paid_dependencies": False,
        "exhaustion_behaviour": "hard_stop",
        "quotas": [{"metric": "requests", "amount": 100000}],
        "confidence": confidence_score,
        "confidence_signals": {
            "official": True,
            "evidence_backed": True,
            "deterministic": True,
            "reproducible": True,
            "no_contradiction": True,
            "completeness": 0.8,
            "freshness": 0.9,
        },
        "classification": {
            "zero_cost_class": "Z0_TRUE_FREE",
            "reasons": ["No credit card required", "No paid dependencies"],
            "blocking_conditions": [],
        },
        "gate": {
            "decision": "publish",
            "automatic_threshold": 0.90,
            "uncertain_threshold": 0.70,
            "reasons": ["deterministic numeric validation passed"],
        },
    }


def _build_graph(*, fetched_at: datetime | None = _UNSET, schedule: str | None = "daily") -> dict:
    """Construct a transient (unpersisted) published Cloudflare-like graph.

    The graph DECLARES its own evidence currency rather than leaning on a
    permissive default in production code (the same correction F008 S5 made to
    ``tests/support/synthetic.py``). ``fetched_at`` defaults to "just now", so
    the fixture is genuinely fresh and the freshness assertions below are earned
    rather than assumed; pass an old timestamp for the expired arm, or ``None``
    for the "no fetch time at all" arm.
    """

    if fetched_at is _UNSET:
        fetched_at = datetime.now(UTC)
    provider = Provider(
        slug="cloudflare",
        name="Cloudflare",
        type="commercial",
        official_domains=["cloudflare.com"],
        source_health="ok",
    )
    provider.id = 1

    category = Category(slug="serverless", name="Serverless")
    category.id = 1

    svc = Service(
        provider_id=1,
        category_id=1,
        canonical_name="Workers",
        deployment_model="managed",
    )
    svc.id = 10
    provider.services.append(svc)

    offer = Offer(
        service_id=10,
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        status="active",
        requires_card=False,
        has_paid_dependencies=False,
    )
    offer.id = 100
    svc.offers.append(offer)

    version = OfferVersion(
        offer_id=100,
        version_number=1,
        content_hash="hash-v1",
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        material_facts=_material_facts(),
    )
    version.id = 1000
    offer.versions.append(version)

    quota = Quota(
        offer_version_id=1000,
        metric="requests",
        amount=100000,
        unit="request",
        reset_period="day",
        behaviour="hard",
        exhaustion_behaviour="hard_stop",
    )
    quota.id = 400
    version.quotas.append(quota)

    source = Source(
        slug="cloudflare-docs",
        adapter_type="html",
        trust_level="official_docs",
        official=True,
        endpoint="https://developers.cloudflare.com/workers/platform/pricing/",
        schedule=schedule,
    )
    source.id = 5
    snapshot = Snapshot(
        source_id=5,
        content_location="s3://snapshots/cf-1",
        mime_type="text/html",
        content_hash="snap-hash",
        fetched_at=fetched_at,
    )
    snapshot.id = 7
    snapshot.source = source

    evidence = Evidence(
        source_id=5,
        offer_version_id=1000,
        snapshot_id=7,
        official=True,
        url="https://developers.cloudflare.com/workers/platform/pricing/",
        title="Workers pricing",
        excerpt="100,000 requests/day free",
        content_hash="ev-hash",
    )
    evidence.id = 900
    evidence.source = source
    evidence.snapshot = snapshot
    version.evidence.append(evidence)

    change_event = ChangeEvent(
        offer_id=100,
        previous_version_id=None,
        new_version_id=1000,
        change_type="added",
        materiality="material",
        publication_status="published",
    )
    change_event.id = 800

    return {
        "provider": provider,
        "category": category,
        "service": svc,
        "offer": offer,
        "version": version,
        "evidence": evidence,
        "change_event": change_event,
    }


# --------------------------------------------------------------------------- #
# Serialization (service layer)                                               #
# --------------------------------------------------------------------------- #


def test_serialize_provider_summary_aggregates_signals() -> None:
    graph = _build_graph()
    summary = service.serialize_provider_summary(graph["provider"], _graph_currency(graph))
    assert summary.slug == "cloudflare"
    assert summary.service_count == 1
    assert summary.published_offer_count == 1
    # Completeness still comes from the published version's signal: how much of
    # the offer we captured does not decay with the calendar.
    assert summary.completeness == pytest.approx(0.8)
    # Freshness no longer does. It is recomputed from evidence currency at read
    # time, so a just-fetched snapshot reads 1.0 -- NOT the 0.9 frozen into
    # material_facts at publish time. That frozen figure is precisely what let a
    # five-year-expired claim keep reporting full freshness.
    assert graph["version"].material_facts["confidence_signals"]["freshness"] == 0.9
    assert summary.freshness == pytest.approx(1.0)


def test_provider_freshness_follows_the_clock_not_the_frozen_signal() -> None:
    """The frozen signal is constant; the served freshness must not be.

    Same graph, same persisted ``confidence_signals.freshness``, three clocks.
    A surface reading the frozen value would return 0.9 in every column.
    """

    graph = _build_graph()
    frozen = graph["version"].material_facts["confidence_signals"]["freshness"]
    fetched = graph["evidence"].snapshot.fetched_at

    fresh = service.serialize_provider_summary(graph["provider"], _graph_currency(graph, fetched))
    half = service.serialize_provider_summary(
        graph["provider"], _graph_currency(graph, fetched + timedelta(hours=12))
    )
    expired = service.serialize_provider_summary(
        graph["provider"], _graph_currency(graph, fetched + timedelta(days=365 * 5))
    )

    assert frozen == 0.9  # unchanged throughout
    assert fresh.freshness == pytest.approx(1.0)
    assert half.freshness is not None and 0.0 < half.freshness < 1.0
    assert fresh.freshness > half.freshness > (expired.freshness or -1.0)
    # And the expired provider no longer claims currency at all.
    assert expired.evidence_currency.stale is True
    assert expired.evidence_currency.current is False
    assert fresh.evidence_currency.current is True


def test_provider_freshness_is_none_not_zero_when_unchecked() -> None:
    """No fetch time -> no number. ``0.0`` would render as "0%" on the page."""

    graph = _build_graph(fetched_at=None)
    summary = service.serialize_provider_summary(graph["provider"], _graph_currency(graph))
    assert summary.freshness is None
    assert summary.evidence_currency.freshness is None
    assert summary.evidence_currency.checked is False
    # Absence of evidence is not evidence of expiry.
    assert summary.evidence_currency.stale is False
    assert summary.evidence_currency.current is False


def test_serialize_offer_detail_label_primary_numeric_advanced_only() -> None:
    graph = _build_graph()
    detail = service.serialize_offer_detail(
        graph["offer"], {1: graph["category"]}, _graph_currency(graph)
    )
    # Primary confidence field is the plain-language label.
    assert detail.confidence_label == "high"
    # Reasons come straight from material_facts.classification.
    assert "No credit card required" in detail.reasons
    assert detail.zero_cost_class == "Z0_TRUE_FREE"
    assert detail.quotas[0].metric == "requests"
    assert detail.quotas[0].amount == pytest.approx(100000)
    # Numeric score lives ONLY in the advanced block.
    assert detail.advanced.score == pytest.approx(0.93)
    dumped = detail.model_dump()
    assert isinstance(dumped["confidence_label"], str)
    assert "confidence" not in dumped  # no top-level numeric confidence field
    assert dumped["advanced"]["score"] == pytest.approx(0.93)


def test_serialize_offer_detail_unknown_when_facts_missing() -> None:
    graph = _build_graph()
    graph["version"].material_facts = {}
    detail = service.serialize_offer_detail(
        graph["offer"], {1: graph["category"]}, _graph_currency(graph)
    )
    assert detail.confidence_label == "unknown"
    assert detail.reasons == []
    assert detail.advanced.score is None
    assert detail.completeness is None


def test_serialize_offer_evidence_provenance() -> None:
    graph = _build_graph()
    response = service.serialize_offer_evidence(
        graph["offer"], [graph["evidence"]], _graph_currency(graph)
    )
    assert response.offer_version_id == 1000
    assert response.confidence_label == "high"
    assert len(response.evidence) == 1
    row = response.evidence[0]
    assert row.official is True
    assert row.source.official is True
    assert row.snapshot.content_hash == "snap-hash"


def test_serialize_offer_history() -> None:
    graph = _build_graph()
    history = service.serialize_offer_history(
        100, [graph["version"]], [graph["change_event"]], _graph_currency(graph)
    )
    assert [v.version_number for v in history.versions] == [1]
    assert history.change_events[0].change_type == "added"
    assert history.change_events[0].publication_status == "published"


# --------------------------------------------------------------------------- #
# HTTP routes (TestClient + monkeypatched queries)                            #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    graph = _build_graph()

    def _fake_session():
        yield object()

    monkeypatch.setattr(queries, "fetch_providers", lambda session: [graph["provider"]])
    monkeypatch.setattr(
        queries,
        "fetch_provider",
        lambda session, slug: graph["provider"] if slug == "cloudflare" else None,
    )
    monkeypatch.setattr(
        queries,
        "fetch_offer",
        lambda session, offer_id: graph["offer"] if offer_id == 100 else None,
    )
    monkeypatch.setattr(
        queries,
        "fetch_offer_evidence",
        lambda session, *, offer_version_id: [graph["evidence"]]
        if offer_version_id == 1000
        else [],
    )
    monkeypatch.setattr(
        queries,
        "fetch_offer_versions",
        lambda session, *, offer_id: [graph["version"]] if offer_id == 100 else [],
    )
    monkeypatch.setattr(
        queries,
        "fetch_offer_change_events",
        lambda session, *, offer_id: [graph["change_event"]] if offer_id == 100 else [],
    )
    monkeypatch.setattr(
        queries,
        "category_map",
        lambda session, ids: {1: graph["category"]} if 1 in list(ids) else {},
    )
    # The routes acquire their clock here. Patched to derive the verdict from the
    # graph's OWN evidence, so aging the fixture changes what the routes serve --
    # a hardcoded "current" context would make every route assertion vacuous.
    monkeypatch.setattr(
        queries,
        "currency_context",
        lambda session, *, now, offer_version_ids=None: _graph_currency(graph, now),
    )

    app.dependency_overrides[get_session] = _fake_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_list_providers(client: TestClient) -> None:
    resp = client.get("/catalogue/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["slug"] == "cloudflare"
    assert body[0]["published_offer_count"] == 1


def test_get_provider_detail(client: TestClient) -> None:
    resp = client.get("/catalogue/providers/cloudflare")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Cloudflare"
    assert body["official_domains"] == ["cloudflare.com"]


def test_get_provider_404(client: TestClient) -> None:
    resp = client.get("/catalogue/providers/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Provider not found."


def test_category_states(client: TestClient) -> None:
    resp = client.get("/catalogue/providers/cloudflare/category-states")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_slug"] == "cloudflare"
    group = body["categories"][0]
    assert group["category"]["slug"] == "serverless"
    assert group["services"][0]["offers"][0]["zero_cost_class"] == "Z0_TRUE_FREE"


def test_provider_offers(client: TestClient) -> None:
    resp = client.get("/catalogue/providers/cloudflare/offers")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["offer_id"] == 100
    assert body[0]["confidence_label"] == "high"


def test_offer_detail(client: TestClient) -> None:
    resp = client.get("/catalogue/offers/100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["confidence_label"] == "high"
    assert body["advanced"]["score"] == pytest.approx(0.93)
    assert "confidence" not in body


def test_offer_detail_404(client: TestClient) -> None:
    resp = client.get("/catalogue/offers/999")
    assert resp.status_code == 404


def test_offer_evidence(client: TestClient) -> None:
    resp = client.get("/catalogue/offers/100/evidence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence"][0]["official"] is True
    assert body["confidence_label"] == "high"


def test_offer_history(client: TestClient) -> None:
    resp = client.get("/catalogue/offers/100/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["versions"][0]["version_number"] == 1
    assert body["change_events"][0]["change_type"] == "added"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_methods_rejected(client: TestClient, method: str) -> None:
    # Read-only: no mutating verb is allowed on any catalogue endpoint.
    resp = getattr(client, method)("/catalogue/providers")
    assert resp.status_code == 405


@pytest.mark.parametrize(
    "bad_slug",
    ["http://evil.example", "https%3A%2F%2Fx", "..%2Fetc", "UPPER"],
)
def test_slug_rejects_url_like_input(client: TestClient, bad_slug: str) -> None:
    # No user-controlled URL can be smuggled through the slug parameter.
    resp = client.get(f"/catalogue/providers/{bad_slug}")
    assert resp.status_code in (404, 422)


def test_no_community_candidate_fields_exposed(client: TestClient) -> None:
    # Guard: catalogue responses must never surface candidate/discovery fields.
    for path in (
        "/catalogue/providers/cloudflare",
        "/catalogue/offers/100",
        "/catalogue/offers/100/evidence",
    ):
        text = client.get(path).text.lower()
        assert "candidate" not in text
        assert "discovery" not in text


# --------------------------------------------------------------------------- #
# Fail-closed default: a serializer with NO clock must not read as fresh       #
# --------------------------------------------------------------------------- #


def test_a_serializer_given_no_currency_context_fails_closed() -> None:
    """An un-updated call site must degrade to "cannot assert", never to "fresh".

    This is the property that makes adding a NEW catalogue surface safe: forget
    to thread the clock and the surface withholds confidence, rather than
    silently re-acquiring the always-fresh behaviour this slice removed.

    It is asserted here because a mutation control proved it was otherwise
    untested: flipping `currency_for`'s fail-closed default to a current verdict
    killed no test at all. A default nothing exercises is a default that will
    quietly rot.
    """

    graph = _build_graph()  # genuinely fresh evidence

    # PAIRED CONTROL: WITH a context this same graph reads "high" and current.
    with_clock = service.serialize_offer_detail(
        graph["offer"], {1: graph["category"]}, _graph_currency(graph)
    )
    assert with_clock.confidence_label == "high"
    assert with_clock.evidence_currency.current is True

    # WITHOUT one, every currency-derived field withholds rather than asserts.
    no_clock = service.serialize_offer_detail(graph["offer"], {1: graph["category"]})
    assert no_clock.confidence_label == "unknown"
    assert no_clock.evidence_currency.current is False
    assert no_clock.evidence_currency.checked is False
    assert no_clock.evidence_currency.stale is False
    assert no_clock.freshness is None
    assert no_clock.advanced.score is None
    assert no_clock.advanced.signals is None
    # The classification itself is untouched -- we withhold confidence, not facts.
    assert no_clock.zero_cost_class == with_clock.zero_cost_class
    assert no_clock.quotas == with_clock.quotas


def test_every_catalogue_serializer_fails_closed_without_a_clock() -> None:
    """The same property across the whole serializer surface, not just one."""

    graph = _build_graph()
    cat_map = {1: graph["category"]}

    assert service.serialize_provider_summary(graph["provider"]).evidence_currency.current is False
    assert service.serialize_provider_detail(graph["provider"]).evidence_currency.current is False
    assert service.serialize_version(graph["version"]).evidence_currency.current is False
    assert (
        service.serialize_offer_evidence(
            graph["offer"], [graph["evidence"]]
        ).evidence_currency.current
        is False
    )

    summaries = service.serialize_offer_summaries(graph["provider"], cat_map)
    assert summaries and all(s.evidence_currency.current is False for s in summaries)

    states = service.serialize_category_states(graph["provider"], cat_map)
    offers = [o for g in states.categories for s in g.services for o in s.offers]
    assert offers and all(o.evidence_currency.current is False for o in offers)

    history = service.serialize_offer_history(100, [graph["version"]], [graph["change_event"]])
    assert history.versions and all(v.evidence_currency.current is False for v in history.versions)


# --------------------------------------------------------------------------- #
# F008 S7 -- the free-offer COUNT is a claim, and needs the same clock         #
# --------------------------------------------------------------------------- #
#
# A count asserts something in the present tense. "12 truly free" says as much
# about 12 offers as a badge says about one, so /catalogue/categories cannot go
# on publishing it without reference to whether the evidence underneath still
# supports it. Measured on this surface before the fix: across a one-second
# staleness boundary `state` and `derived_state` moved and `free_offer_count`
# did not, so a cell could read "stale" and "1 truly free" simultaneously.
#
# Every test below is PAIRED. A guard that cannot be shown to PERMIT is
# indistinguishable from one that broke the product, and a wrongly-WITHHELD free
# offer is a defect of exactly the same severity as a wrongly-asserted one.


def _matrix_cell(matrix, category_slug: str, provider_slug: str):
    """The one coverage cell for a (category, provider) pair."""

    for row in matrix.categories:
        if row.slug != category_slug:
            continue
        for cell in row.providers:
            if cell.provider_slug == provider_slug:
                return cell
    raise AssertionError(f"no cell for {category_slug}/{provider_slug}")


def _canonical_graph(**kwargs) -> dict:
    """``_build_graph`` with its category moved onto a CANONICAL slug.

    The default fixture uses "serverless", which is not one of the fourteen
    canonical slugs and therefore lands in the uncategorized rollup. Both
    placements are wanted here, so each test says which one it means instead of
    depending on that detail silently.
    """

    graph = _build_graph(**kwargs)
    graph["category"].slug = "serverless-functions"
    return graph


def test_a_current_free_offer_is_counted_as_still_evidenced() -> None:
    """PERMIT ARM. Current evidence -> the evidenced count equals the total."""

    graph = _canonical_graph()
    matrix = service.serialize_category_matrix(
        [graph["provider"]], {1: graph["category"]}, None, _graph_currency(graph)
    )
    cell = _matrix_cell(matrix, "serverless-functions", "cloudflare")

    assert cell.free_offer_count == 1
    assert cell.current_free_offer_count == 1
    assert cell.evidence_currency.current is True
    assert cell.evidence_currency.stale is False


def test_an_expired_free_offer_stops_being_counted_as_still_evidenced() -> None:
    """WITHHOLD ARM. Same rows, later clock -> the evidenced count drops to 0."""

    graph = _canonical_graph()
    fetched = graph["evidence"].snapshot.fetched_at
    expired = service.serialize_category_matrix(
        [graph["provider"]],
        {1: graph["category"]},
        None,
        _graph_currency(graph, fetched + timedelta(days=365 * 5)),
    )
    cell = _matrix_cell(expired, "serverless-functions", "cloudflare")

    assert cell.current_free_offer_count == 0
    assert cell.evidence_currency.stale is True
    assert cell.evidence_currency.current is False
    assert cell.evidence_currency.reason is not None


def test_the_free_offer_total_is_never_reduced_when_the_evidence_expires() -> None:
    """The total must NOT shrink. Hiding a free offer is its own defect.

    Silently reducing "12 truly free" to "9 truly free" conceals three offers
    that really are free, and an omission is invisible to a reader in a way a
    label is not. Both shipped precedents (PR #79, PR #83) display rather than
    omit. So the total is pinned identical across the boundary and only the
    *additional* number moves.
    """

    graph = _canonical_graph()
    fetched = graph["evidence"].snapshot.fetched_at
    args = ([graph["provider"]], {1: graph["category"]}, None)

    current = _matrix_cell(
        service.serialize_category_matrix(*args, _graph_currency(graph, fetched)),
        "serverless-functions",
        "cloudflare",
    )
    expired = _matrix_cell(
        service.serialize_category_matrix(
            *args, _graph_currency(graph, fetched + timedelta(days=365 * 5))
        ),
        "serverless-functions",
        "cloudflare",
    )

    assert current.free_offer_count == expired.free_offer_count == 1
    assert current.published_offer_count == expired.published_offer_count == 1
    # ...and the qualification is what moved instead.
    assert current.current_free_offer_count == 1
    assert expired.current_free_offer_count == 0


def test_an_unchecked_free_offer_is_not_counted_as_still_evidenced() -> None:
    """ "We could not look" must never read as "still free".

    This is the arm that decides which instrument the surface uses. The
    ``stale_offer_version_ids`` set already threaded into this endpoint CANNOT
    represent this case -- its own docstring says evidence with no ``fetched_at``
    "is still not stale" -- so a count built from that projection would report
    this offer as supported. Built from ``is_publishable_free_claim`` it does not.
    """

    graph = _canonical_graph(fetched_at=None)
    matrix = service.serialize_category_matrix(
        [graph["provider"]], {1: graph["category"]}, None, _graph_currency(graph)
    )
    cell = _matrix_cell(matrix, "serverless-functions", "cloudflare")

    assert cell.free_offer_count == 1
    assert cell.current_free_offer_count == 0
    assert cell.evidence_currency.checked is False
    # Absence of evidence is not evidence of expiry.
    assert cell.evidence_currency.stale is False
    assert cell.evidence_currency.current is False


def test_a_category_matrix_with_no_clock_counts_no_free_offer_as_current() -> None:
    """FAIL-CLOSED. Forgetting to thread the clock withholds, never asserts.

    Paired with the permit arm on the same genuinely-fresh graph, so this cannot
    pass merely because the fixture is stale.
    """

    graph = _canonical_graph()

    with_clock = _matrix_cell(
        service.serialize_category_matrix(
            [graph["provider"]], {1: graph["category"]}, None, _graph_currency(graph)
        ),
        "serverless-functions",
        "cloudflare",
    )
    no_clock = _matrix_cell(
        service.serialize_category_matrix([graph["provider"]], {1: graph["category"]}),
        "serverless-functions",
        "cloudflare",
    )

    assert with_clock.current_free_offer_count == 1
    assert no_clock.current_free_offer_count == 0
    assert no_clock.evidence_currency.checked is False
    assert no_clock.evidence_currency.current is False
    # The facts themselves are untouched -- currency is withheld, not data.
    assert no_clock.free_offer_count == with_clock.free_offer_count
    assert no_clock.published_offer_count == with_clock.published_offer_count
    assert no_clock.state == with_clock.state


def test_the_uncategorized_rollup_reports_its_own_evidence_currency() -> None:
    """The rollup had NOWHERE to hang a currency signal; now it has one.

    ``_build_graph``'s category slug is non-canonical, so this offer lands in the
    uncategorized rollup -- the surface that carried a completely unqualified
    "N truly free" with no state field of any kind.
    """

    graph = _build_graph()  # NON-canonical slug on purpose
    fetched = graph["evidence"].snapshot.fetched_at
    args = ([graph["provider"]], {1: graph["category"]}, None)

    current = service.serialize_category_matrix(
        *args, _graph_currency(graph, fetched)
    ).uncategorized
    expired = service.serialize_category_matrix(
        *args, _graph_currency(graph, fetched + timedelta(days=365 * 5))
    ).uncategorized

    assert len(current) == len(expired) == 1
    # PERMIT arm.
    assert current[0].free_offer_count == 1
    assert current[0].current_free_offer_count == 1
    assert current[0].evidence_currency.current is True
    # WITHHOLD arm -- and the total still does not shrink.
    assert expired[0].free_offer_count == 1
    assert expired[0].current_free_offer_count == 0
    assert expired[0].evidence_currency.stale is True


def test_the_uncategorized_rollup_has_no_coverage_state_field() -> None:
    """A design decision, pinned so it cannot drift back.

    "Uncategorised" is not one of the fourteen canonical categories. Giving this
    rollup a COVERAGE_STATES value would assert a coverage claim about a bucket
    the taxonomy cannot name -- the same guess F008 S2 removed when it deleted
    ``published == 0 -> not_offered``. What it needed was an EVIDENCE signal, and
    that is what it got.
    """

    graph = _build_graph()
    rollup = service.serialize_category_matrix(
        [graph["provider"]], {1: graph["category"]}, None, _graph_currency(graph)
    ).uncategorized[0]

    fields = set(rollup.model_dump())
    assert "state" not in fields
    assert "derived_state" not in fields
    assert "declared_state" not in fields
    assert "evidence_currency" in fields
    assert "current_free_offer_count" in fields


def test_a_cell_is_only_as_current_as_its_least_current_claim() -> None:
    """The rollup is `worst()`, not an average: one fresh offer must not mask one stale.

    Two published free offers in the same cell, one current and one long expired.
    An averaging (or first-wins) rollup would report the cell as current.
    """

    graph = _canonical_graph()
    fetched = graph["evidence"].snapshot.fetched_at
    service_obj = graph["service"]

    second = Offer(
        service_id=10,
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        status="active",
        requires_card=False,
        has_paid_dependencies=False,
    )
    second.id = 101
    second_version = OfferVersion(
        offer_id=101,
        version_number=1,
        content_hash="hash-v1-b",
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        material_facts=_material_facts(),
    )
    second_version.id = 1001
    second.versions.append(second_version)
    service_obj.offers.append(second)

    fresh = assess_currency(fetched, fetched, "daily")
    stale = assess_currency(fetched, fetched + timedelta(days=365 * 5), "daily")
    ctx = CurrencyContext(
        index={
            (ANCHOR_OFFER_VERSION, 1000): fresh,
            (ANCHOR_OFFER_VERSION, 1001): stale,
        },
        now=fetched,
    )

    cell = _matrix_cell(
        service.serialize_category_matrix([graph["provider"]], {1: graph["category"]}, None, ctx),
        "serverless-functions",
        "cloudflare",
    )

    assert cell.free_offer_count == 2
    # Exactly one of the two is still evidenced -- not zero, not both.
    assert cell.current_free_offer_count == 1
    # The CELL verdict takes the worse of the two.
    assert cell.evidence_currency.stale is True
    assert cell.evidence_currency.current is False


def test_an_offers_currency_is_read_from_its_latest_version() -> None:
    """Superseded versions must not decide a current claim.

    An offer's claim rests on its LATEST version, matching every other catalogue
    surface. Keying on an older version instead would let a superseded snapshot
    withhold a genuinely current free offer -- the wrongly-withheld direction.
    """

    graph = _canonical_graph()
    fetched = graph["evidence"].snapshot.fetched_at
    offer = graph["offer"]

    older = OfferVersion(
        offer_id=100,
        version_number=0,
        content_hash="hash-v0",
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        material_facts=_material_facts(),
    )
    older.id = 999
    offer.versions.insert(0, older)

    assert queries.latest_version(offer).id == 1000, "fixture precondition"

    ctx = CurrencyContext(
        index={
            # The superseded version is long expired...
            (ANCHOR_OFFER_VERSION, 999): assess_currency(
                fetched, fetched + timedelta(days=365 * 5), "daily"
            ),
            # ...while the latest one is current.
            (ANCHOR_OFFER_VERSION, 1000): assess_currency(fetched, fetched, "daily"),
        },
        now=fetched,
    )

    cell = _matrix_cell(
        service.serialize_category_matrix([graph["provider"]], {1: graph["category"]}, None, ctx),
        "serverless-functions",
        "cloudflare",
    )

    assert cell.current_free_offer_count == 1
    assert cell.evidence_currency.current is True


# --------------------------------------------------------------------------- #
# The bucket STALE FLAG follows the latest version too                        #
# --------------------------------------------------------------------------- #
#
# S7 gave this row's free-offer COUNT a clock and left `has_stale_evidence` --
# the bucket-wide flag driving `derived_state` -- scanning EVERY version of every
# published offer, superseded ancestors included. Because
# `derive_coverage_state` returns "stale" BEFORE it can return "verified_free",
# one expired ancestor anywhere in a bucket cost every offer in that bucket its
# public verified_free badge.
#
# MEASURED, not assumed. Against the committed corpus published for real (7
# provider configs, 6 published offers), every offer holds exactly ONE version,
# so the two rules cannot diverge and NO real bucket differs today: 0 differences
# across 105 bucket-observations (5 buckets x 21 clocks spanning every distinct
# staleness boundary +/-1s). The defect was LATENT. It is reachable, though: a
# second publish whose content hash differs creates version 2 via
# `publisher.py:389`, and the divergence then appears immediately.
#
# Both arms are mandatory and BOTH carry their own mutation. A guard that cannot
# be shown to PERMIT is indistinguishable from one that broke the product, and
# here the permit arm is also what stops the fix over-correcting into wrongly
# asserting freshness.


def _versioned_graph(*, older_id: int = 999, latest_id: int = 1000) -> dict:
    """``_canonical_graph`` plus a SUPERSEDED ancestor version on the same offer.

    Mirrors the two-version shape a real offer acquires the moment a provider
    changes a page: version 1 is superseded, version 2 carries the live claim.
    """

    graph = _canonical_graph()
    older = OfferVersion(
        offer_id=100,
        version_number=0,
        content_hash="hash-v0",
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        material_facts=_material_facts(),
    )
    older.id = older_id
    graph["offer"].versions.insert(0, older)
    assert queries.latest_version(graph["offer"]).id == latest_id, "fixture precondition"
    graph["older_version"] = older
    return graph


def _coverage_context(stale_ids: set[int]) -> queries.CoverageSignalContext:
    """A signal context carrying only the staleness projection under test."""

    return queries.CoverageSignalContext(
        declarations={},
        conflicted_services=frozenset(),
        stale_offer_version_ids=frozenset(stale_ids),
    )


def test_a_superseded_stale_version_does_not_withhold_the_bucket_badge() -> None:
    """PERMIT ARM. A stale ANCESTOR must not cost a fresh bucket its badge.

    The offer's claim rests on its latest version. Applying a superseded
    version's expiry to it is not conservatism but a category error: it says
    nothing about the claim actually being made, and because "stale" outranks
    "verified_free" it withholds the free badge from EVERY offer in the bucket.
    A wrongly-withheld free offer is a defect of the same severity as a
    wrongly-asserted one, and it is the direction a reader cannot see.

    FAILS at ab3dfc00 (reports "stale"); passes once the flag reads latest_id.
    """

    graph = _versioned_graph()
    cell = _matrix_cell(
        service.serialize_category_matrix(
            [graph["provider"]],
            {1: graph["category"]},
            _coverage_context({999}),  # the SUPERSEDED version is the stale one
            _graph_currency(graph),
        ),
        "serverless-functions",
        "cloudflare",
    )

    assert cell.derived_state == "verified_free"
    assert cell.free_offer_count == 1
    assert cell.current_free_offer_count == 1


def test_a_stale_latest_version_still_marks_the_bucket_stale() -> None:
    """WITHHOLD ARM. The fix must not over-correct into never-stale.

    Same fixture, same ancestor, but now it is the LATEST version whose evidence
    has expired. The bucket must still report "stale" -- an expired claim is a
    guess, and no amount of tidying the ancestor rule may buy freshness the
    evidence does not support.

    Passes before and after, so it is made load-bearing by mutation M-over
    (`flag[1] or False`) rather than by assertion.
    """

    graph = _versioned_graph()
    cell = _matrix_cell(
        service.serialize_category_matrix(
            [graph["provider"]],
            {1: graph["category"]},
            _coverage_context({1000}),  # the LATEST version is the stale one
            _graph_currency(graph),
        ),
        "serverless-functions",
        "cloudflare",
    )

    assert cell.derived_state == "stale"


def test_one_offer_whose_latest_is_stale_still_marks_the_whole_bucket() -> None:
    """The ACROSS-OFFERS axis is deliberately unchanged.

    Two coarsenesses live in this flag and conflating them is how a fix
    overshoots. Across VERSIONS of one offer, an ancestor must not speak for the
    current claim -- that is the defect. Across OFFERS, one offer whose LATEST
    version is stale marks the whole cell -- that is what bucket-wide means, and
    this slice must leave it exactly as it was.
    """

    graph = _canonical_graph()
    second = Offer(
        service_id=10,
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        status="active",
        requires_card=False,
        has_paid_dependencies=False,
    )
    second.id = 101
    second_version = OfferVersion(
        offer_id=101,
        version_number=1,
        content_hash="hash-v1-b",
        offer_type="always_free",
        zero_cost_class="Z0_TRUE_FREE",
        material_facts=_material_facts(),
    )
    second_version.id = 1001
    second.versions.append(second_version)
    graph["service"].offers.append(second)

    cell = _matrix_cell(
        service.serialize_category_matrix(
            [graph["provider"]],
            {1: graph["category"]},
            # The FIRST offer's latest is current; the SECOND offer's latest is not.
            _coverage_context({1001}),
            _graph_currency(graph),
        ),
        "serverless-functions",
        "cloudflare",
    )

    assert cell.free_offer_count == 2
    assert cell.derived_state == "stale"


def test_a_cell_cannot_report_current_evidence_and_a_stale_state_at_once() -> None:
    """One cell, one answer about which versions back the claim.

    `evidence_currency` is a rollup over `bucket_versions`, which holds LATEST
    ids only, while `has_stale_evidence` scanned every id. The two fields of the
    same cell therefore disagreed about the same question, and a reader saw
    "evidence is current" printed beside "state: stale" in one response.

    This asserts the agreement rather than either value on its own, so it stays
    meaningful if the displayed labels are ever renamed.
    """

    graph = _versioned_graph()
    cell = _matrix_cell(
        service.serialize_category_matrix(
            [graph["provider"]],
            {1: graph["category"]},
            _coverage_context({999}),
            _graph_currency(graph),
        ),
        "serverless-functions",
        "cloudflare",
    )

    assert cell.evidence_currency.current is True
    assert cell.derived_state != "stale", (
        "a cell reporting current evidence must not simultaneously report a stale state"
    )
