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
