"""Offline unit tests for the F006 catalogue query API (search + categories + compare).

These tests never touch a live database. They exercise:

* the conservative, fail-closed quota-unit normalization,
* the search input validation + LIKE-escaping (no pattern injection),
* the ORM -> schema serialization for search results, the category coverage
  matrix, and the normalized compare view (using an in-memory multi-provider
  graph), and
* the HTTP routes via ``TestClient`` with ``queries`` / ``search`` monkeypatched --
  asserting GET-only behaviour, hostile-input handling (bad enum -> 422, oversize
  / non-integer compare set -> 422, unknown id -> 404), multi-provider behaviour,
  and that no community/candidate data is present.
"""

from __future__ import annotations

import pytest
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
)
from app.read_api import normalize, queries, search, service
from app.read_api.taxonomy import CATEGORY_TAXONOMY
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Quota-unit normalization (pure, fail-closed)                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("amount", "unit", "canonical"),
    [
        (1, "KB", 1000.0),
        (1, "MB", 1000.0**2),
        (2, "GB", 2 * 1000.0**3),
        (1, "KiB", 1024.0),
        (1, "GiB", 1024.0**3),
        (5, "B", 5.0),
    ],
)
def test_normalize_data_sizes(amount: float, unit: str, canonical: float) -> None:
    result = normalize.normalize_amount(amount, unit)
    assert result.normalized is True
    assert result.canonical_unit == normalize.BYTE_UNIT
    assert result.dimension == "data_size"
    assert result.canonical_amount == pytest.approx(canonical)


def test_normalize_decimal_and_binary_do_not_collapse() -> None:
    gb = normalize.normalize_amount(1, "GB")
    gib = normalize.normalize_amount(1, "GiB")
    assert gb.canonical_amount != gib.canonical_amount


def test_normalize_count_units_pass_through() -> None:
    result = normalize.normalize_amount(100_000, "requests")
    assert result.normalized is True
    assert result.canonical_unit == normalize.COUNT_UNIT
    assert result.canonical_amount == pytest.approx(100_000)


@pytest.mark.parametrize(
    ("amount", "unit"),
    [
        (None, "GB"),  # unknown amount
        (10, None),  # missing unit
        (10, "   "),  # blank unit
        (10, "vcpu-hours"),  # unrecognised unit
        (10, "GB/month"),  # rate unit not recognised
    ],
)
def test_normalize_fails_closed(amount: object, unit: str | None) -> None:
    result = normalize.normalize_amount(amount, unit)
    assert result.normalized is False
    assert result.canonical_amount is None
    assert result.canonical_unit is None
    assert result.note  # a human-readable explanation is present


def test_normalize_boolean_is_not_an_amount() -> None:
    # A bool must never be silently coerced to 1/0.
    assert normalize.normalize_amount(True, "GB").normalized is False


def test_comparable_only_same_dimension() -> None:
    gb = normalize.normalize_amount(1, "GB")
    mb = normalize.normalize_amount(1, "MB")
    reqs = normalize.normalize_amount(1, "requests")
    unknown = normalize.normalize_amount(1, "vcpu")
    assert normalize.comparable(gb, mb) is True
    assert normalize.comparable(gb, reqs) is False
    assert normalize.comparable(gb, unknown) is False


# --------------------------------------------------------------------------- #
# Search input validation                                                     #
# --------------------------------------------------------------------------- #


def test_escape_like_neutralises_wildcards() -> None:
    assert search._escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"


def test_build_params_trims_and_blanks_to_none() -> None:
    assert search.build_params(q="   ").q is None
    assert search.build_params(q="  cloud  ").q == "cloud"


def test_build_params_rejects_oversize_q() -> None:
    with pytest.raises(search.SearchValidationError):
        search.build_params(q="x" * (search.MAX_Q_LENGTH + 1))


@pytest.mark.parametrize("page", [0, -1, search.MAX_PAGE + 1])
def test_build_params_rejects_bad_page(page: int) -> None:
    with pytest.raises(search.SearchValidationError):
        search.build_params(page=page)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("zero_cost_class", "NOPE"),
        ("offer_type", "totally_free"),
        ("status", "banished"),
    ],
)
def test_build_params_rejects_bad_enum(field: str, value: str) -> None:
    with pytest.raises(search.SearchValidationError):
        search.build_params(**{field: value})


def test_build_params_accepts_valid_enums() -> None:
    params = search.build_params(
        zero_cost_class="Z0_TRUE_FREE",
        offer_type="always_free",
        status="active",
        commercial_use=True,
    )
    assert params.zero_cost_class == "Z0_TRUE_FREE"
    assert params.commercial_use is True


# --------------------------------------------------------------------------- #
# In-memory multi-provider published graph                                    #
# --------------------------------------------------------------------------- #


def _facts(confidence_score: float = 0.93) -> dict:
    return {
        "confidence": confidence_score,
        "confidence_signals": {"completeness": 0.8, "freshness": 0.9},
        "classification": {
            "zero_cost_class": "Z0_TRUE_FREE",
            "reasons": ["No credit card required"],
            "blocking_conditions": [],
        },
        "gate": {"automatic_threshold": 0.90, "uncertain_threshold": 0.70},
    }


def _make_offer(
    *,
    offer_id: int,
    service: Service,
    zero_cost_class: str = "Z0_TRUE_FREE",
    offer_type: str = "always_free",
    quota: tuple[float, str] | None = (100_000, "requests"),
    commercial: bool = True,
) -> Offer:
    offer = Offer(
        service_id=service.id,
        offer_type=offer_type,
        zero_cost_class=zero_cost_class,
        status="active",
        requires_card=False,
        has_paid_dependencies=False,
        commercial_use_allowed=commercial,
        personal_use_allowed=True,
    )
    offer.id = offer_id
    service.offers.append(offer)

    version = OfferVersion(
        offer_id=offer_id,
        version_number=1,
        content_hash=f"hash-{offer_id}",
        offer_type=offer_type,
        zero_cost_class=zero_cost_class,
        material_facts=_facts(),
    )
    version.id = offer_id * 10
    offer.versions.append(version)

    if quota is not None:
        amount, unit = quota
        q = Quota(
            offer_version_id=version.id,
            metric="requests",
            amount=amount,
            unit=unit,
            reset_period="day",
            behaviour="hard",
            exhaustion_behaviour="hard_stop",
        )
        q.id = offer_id * 100
        version.quotas.append(q)

    evidence = Evidence(
        source_id=1,
        offer_version_id=version.id,
        snapshot_id=1,
        official=True,
        url="https://example.com/pricing",
        title="Pricing",
        excerpt="free",
        content_hash=f"ev-{offer_id}",
    )
    evidence.id = offer_id * 1000
    version.evidence.append(evidence)
    return offer


def _multi_graph() -> dict:
    """Two providers: a categorized free offer + a paid one, and a synthetic one."""

    serverless = Category(slug="serverless-functions", name="Serverless functions")
    serverless.id = 1

    # Provider A: cloudflare with a categorized free serverless offer.
    cf = Provider(slug="cloudflare", name="Cloudflare", type="commercial", source_health="ok")
    cf.id = 1
    cf_svc = Service(
        provider_id=1, category_id=1, canonical_name="Workers", deployment_model="managed"
    )
    cf_svc.id = 10
    cf.services.append(cf_svc)
    cf_offer = _make_offer(offer_id=100, service=cf_svc, quota=(100_000, "requests"))

    # Provider B: a clearly-synthetic fixture provider with an uncategorized,
    # non-free offer (proves multi-provider + honest uncategorized rollup).
    ex = Provider(
        slug="example-two", name="Example Two (synthetic)", type="commercial", source_health="ok"
    )
    ex.id = 2
    ex_svc = Service(
        provider_id=2, category_id=None, canonical_name="Example Store", deployment_model="managed"
    )
    ex_svc.id = 20
    ex.services.append(ex_svc)
    ex_offer = _make_offer(
        offer_id=200,
        service=ex_svc,
        zero_cost_class="Z1_BILLING_EXPOSURE",
        quota=(5, "GB"),
        commercial=False,
    )

    return {
        "category": serverless,
        "providers": [cf, ex],
        "cf_offer": cf_offer,
        "ex_offer": ex_offer,
        "cat_map": {1: serverless},
    }


# --------------------------------------------------------------------------- #
# Serialization (service layer)                                               #
# --------------------------------------------------------------------------- #


def test_serialize_search_response_pagination_and_labels() -> None:
    graph = _multi_graph()
    page = search.SearchPage(
        offers=[graph["cf_offer"], graph["ex_offer"]], total=25, page=1, page_size=20
    )
    params = search.build_params(q="workers")
    resp = service.serialize_search_response(page, params, graph["cat_map"])
    assert resp.total_results == 25
    assert resp.total_pages == 2  # ceil(25 / 20)
    assert resp.filters.q == "workers"
    slugs = {item.provider_slug for item in resp.results}
    assert slugs == {"cloudflare", "example-two"}
    cf_item = next(i for i in resp.results if i.provider_slug == "cloudflare")
    assert cf_item.category is not None and cf_item.category.slug == "serverless-functions"
    assert cf_item.confidence_label == "high"


def test_serialize_category_matrix_always_14_and_states() -> None:
    graph = _multi_graph()
    matrix = service.serialize_category_matrix(graph["providers"], graph["cat_map"])
    assert len(matrix.categories) == len(CATEGORY_TAXONOMY) == 14
    assert [row.ordinal for row in matrix.categories] == list(range(1, 15))
    assert matrix.provider_slugs == ["cloudflare", "example-two"]

    serverless_row = next(r for r in matrix.categories if r.slug == "serverless-functions")
    by_provider = {c.provider_slug: c for c in serverless_row.providers}
    assert by_provider["cloudflare"].state == "verified_free"
    assert by_provider["cloudflare"].free_offer_count == 1
    # The synthetic provider offers nothing in this category.
    assert by_provider["example-two"].state == "not_offered"

    # The synthetic provider's uncategorized, non-free offer is surfaced honestly.
    uncat = {u.provider_slug: u for u in matrix.uncategorized}
    assert "example-two" in uncat
    assert uncat["example-two"].published_offer_count == 1
    assert uncat["example-two"].free_offer_count == 0
    assert "cloudflare" not in uncat  # cloudflare's only offer is categorized


def test_serialize_compare_normalizes_and_fails_closed() -> None:
    graph = _multi_graph()
    # cloudflare quota is a count unit (normalizes); add an offer with an
    # unnormalizable unit to prove fail-closed behaviour.
    weird_svc = graph["providers"][0].services[0]
    weird = _make_offer(offer_id=300, service=weird_svc, quota=(3, "vcpu-hours"))
    compare = service.serialize_compare([100, 300], [graph["cf_offer"], weird], graph["cat_map"])
    assert [o.offer_id for o in compare.offers] == [100, 300]

    reqs_quota = compare.offers[0].quotas[0]
    assert reqs_quota.normalized is True
    assert reqs_quota.canonical_unit == normalize.COUNT_UNIT

    weird_quota = compare.offers[1].quotas[0]
    assert weird_quota.normalized is False
    assert weird_quota.canonical_amount is None
    assert weird_quota.normalization_note

    # Confidence stays label-primary; the numeric score is only in advanced{}.
    dumped = compare.offers[0].model_dump()
    assert dumped["confidence_label"] == "high"
    assert dumped["advanced"]["score"] == pytest.approx(0.93)
    assert compare.offers[0].evidence_count == 1


# --------------------------------------------------------------------------- #
# HTTP routes (TestClient + monkeypatched queries / search)                   #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    graph = _multi_graph()
    offers_by_id = {100: graph["cf_offer"], 200: graph["ex_offer"]}

    def _fake_session():
        yield object()

    def _fake_search(session, params):  # noqa: ANN001
        # Echo the provider filter to prove filters reach the query layer.
        offers = [graph["cf_offer"], graph["ex_offer"]]
        if params.provider is not None:
            offers = [o for o in offers if o.service.provider.slug == params.provider]
        return search.SearchPage(offers=offers, total=len(offers), page=params.page, page_size=20)

    monkeypatch.setattr(search, "search_published_offers", _fake_search)
    monkeypatch.setattr(queries, "fetch_providers", lambda session: graph["providers"])
    monkeypatch.setattr(
        queries, "category_map_for_providers", lambda session, providers: graph["cat_map"]
    )
    monkeypatch.setattr(
        queries,
        "category_map",
        lambda session, ids: {1: graph["category"]} if 1 in list(ids) else {},
    )
    monkeypatch.setattr(
        queries,
        "fetch_offers_by_ids",
        lambda session, ids: {i: offers_by_id[i] for i in ids if i in offers_by_id},
    )

    app.dependency_overrides[get_session] = _fake_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_search_returns_multiple_providers(client: TestClient) -> None:
    resp = client.get("/catalogue/search", params={"q": "example"})
    assert resp.status_code == 200
    body = resp.json()
    slugs = {r["provider_slug"] for r in body["results"]}
    assert slugs == {"cloudflare", "example-two"}
    assert body["page"] == 1
    assert body["page_size"] == 20


def test_search_provider_filter_composes(client: TestClient) -> None:
    resp = client.get("/catalogue/search", params={"provider": "cloudflare"})
    assert resp.status_code == 200
    body = resp.json()
    assert {r["provider_slug"] for r in body["results"]} == {"cloudflare"}
    assert body["filters"]["provider"] == "cloudflare"


def test_search_rejects_bad_enum(client: TestClient) -> None:
    resp = client.get("/catalogue/search", params={"zero_cost_class": "BOGUS"})
    assert resp.status_code == 422


def test_search_rejects_url_like_provider_filter(client: TestClient) -> None:
    resp = client.get("/catalogue/search", params={"provider": "http://evil.example"})
    assert resp.status_code == 422


def test_search_neutralises_hostile_q(client: TestClient) -> None:
    # A URL / SQL-ish / traversal q is accepted as literal text, never fetched
    # or interpreted (it just yields whatever the query matches).
    for hostile in ("https://evil.example/x", "'; DROP TABLE offers;--", "../../etc/passwd"):
        resp = client.get("/catalogue/search", params={"q": hostile})
        assert resp.status_code == 200


def test_categories_matrix_endpoint(client: TestClient) -> None:
    resp = client.get("/catalogue/categories")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["categories"]) == 14
    assert body["provider_slugs"] == ["cloudflare", "example-two"]


def test_compare_endpoint_normalized(client: TestClient) -> None:
    resp = client.get("/catalogue/compare", params={"offers": "100,200"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["offer_ids"] == [100, 200]
    assert {o["provider_slug"] for o in body["offers"]} == {"cloudflare", "example-two"}
    # example-two's 5 GB quota normalizes to bytes.
    ex = next(o for o in body["offers"] if o["provider_slug"] == "example-two")
    assert ex["quotas"][0]["normalized"] is True
    assert ex["quotas"][0]["canonical_unit"] == "byte"


def test_compare_unknown_id_404(client: TestClient) -> None:
    resp = client.get("/catalogue/compare", params={"offers": "100,999"})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "offers",
    ["100", "", "100,100", "1,2,3,4,5,6,7", "100,abc", "100,../etc", "100,99999999999"],
)
def test_compare_rejects_bad_id_sets(client: TestClient, offers: str) -> None:
    resp = client.get("/catalogue/compare", params={"offers": offers})
    assert resp.status_code == 422


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize(
    "path", ["/catalogue/search", "/catalogue/categories", "/catalogue/compare"]
)
def test_new_endpoints_are_get_only(client: TestClient, method: str, path: str) -> None:
    resp = getattr(client, method)(path)
    assert resp.status_code == 405


def test_new_endpoints_never_expose_candidate_data(client: TestClient) -> None:
    for path, params in (
        ("/catalogue/search", {"q": "e"}),
        ("/catalogue/categories", {}),
        ("/catalogue/compare", {"offers": "100,200"}),
    ):
        text = client.get(path, params=params).text.lower()
        assert "candidate" not in text
        assert "discovery" not in text
