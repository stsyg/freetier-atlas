"""Offline contract tests for the Vercel OFFICIAL provider slice (F008 P2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.classify.engine import OfferFacts, classify
from app.config import load_and_validate
from app.ingest import resolve_profile

from tests.support.fixtures import (
    available_cases,
    build_fixture_adapter,
    load_case,
    run_extraction_case,
)

PROVIDER = "vercel"
ADAPTER = "html"
CONFIG = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "examples"
    / "providers"
    / "vercel.example.yaml"
)
CANONICAL_CATEGORIES = frozenset(
    {
        "compute-vms",
        "containers-app-hosting",
        "serverless-functions",
        "relational-databases",
        "nosql-key-value",
        "object-file-storage",
        "networking-cdn-dns",
        "queues-messaging-jobs",
        "auth-identity",
        "cicd-source-control",
        "monitoring-logs-tracing",
        "ai-inference-embeddings",
        "email-notifications-comms",
        "secrets-config-devtools",
    }
)
OFFICIAL_CASES = (
    "vercel-hobby-plan",
    "vercel-blob-pricing",
    "vercel-queues-pricing",
    "vercel-pro-trial",
    "vercel-storage-boundary",
    "vercel-ai-gateway-promotion",
)
NUMERIC_CASES = (
    "vercel-hobby-plan",
    "vercel-blob-pricing",
    "vercel-queues-pricing",
    "vercel-pro-trial",
)
NON_QUOTA_KEYS = frozenset(
    {"service", "offer_type", "requires_card", "has_paid_dependencies", "exhaustion_behaviour"}
)


@pytest.fixture(scope="module")
def config():
    return load_and_validate(CONFIG)


def test_the_corpus_covers_every_source_and_all_seven_case_shapes() -> None:
    cases = set(available_cases(PROVIDER, ADAPTER))
    assert set(OFFICIAL_CASES) <= cases
    assert {"unchanged", "changed", "partial", "malformed", "contradictory"} <= cases


@pytest.mark.parametrize("case", available_cases(PROVIDER, ADAPTER))
def test_every_fixture_extracts_exactly_as_captured(case: str) -> None:
    run_extraction_case(PROVIDER, ADAPTER, case, official_domains=("vercel.com",))


@pytest.mark.parametrize("case", NUMERIC_CASES)
def test_every_published_number_appears_verbatim_in_its_capture(case: str) -> None:
    fixture = load_case(PROVIDER, ADAPTER, case)
    source = fixture.source_path.read_text(encoding="utf-8")
    checked = 0
    for candidate in fixture.expected_candidates:
        for key, value in candidate["facts"].items():
            if key in NON_QUOTA_KEYS or not isinstance(value, str):
                continue
            if not any(ch.isdigit() for ch in value):
                continue
            assert value in source, f"{case}: untraceable {key}={value!r}"
            checked += 1
    assert checked


def test_partial_input_withholds_an_unknown_offer_type_before_classification() -> None:
    _, candidates = run_extraction_case(
        PROVIDER, ADAPTER, "partial", official_domains=("vercel.com",)
    )
    (candidate,) = candidates
    assert candidate.facts["offer_type"] is None
    assert candidate.facts["projects"] == "200"


def test_storage_boundary_does_not_invent_a_vercel_database_offer() -> None:
    fixture, candidates = run_extraction_case(
        PROVIDER, ADAPTER, "vercel-storage-boundary", official_domains=("vercel.com",)
    )
    (candidate,) = candidates
    assert candidate.facts["offer_type"] is None
    assert "Neon" in candidate.facts["marketplace_providers"]
    adapter = build_fixture_adapter(fixture, official_domains=("vercel.com",))
    assert "offer_type" in " ".join(adapter.validate(candidate))


@pytest.mark.parametrize(
    "name",
    [
        "vercel_hobby_plan",
        "vercel_blob_pricing",
        "vercel_queues_pricing",
        "vercel_pro_trial",
        "vercel_storage_boundary",
        "vercel_ai_gateway_promotion",
    ],
)
def test_profiles_are_registered_as_data_and_require_canonical_offer_type(name: str) -> None:
    profile = resolve_profile(name)
    assert profile.name == name
    assert profile.required_fields == ("service", "offer_type")


def _classification(case: str, index: int = 0):
    facts = load_case(PROVIDER, ADAPTER, case).expected_candidates[index]["facts"]
    return classify(
        OfferFacts(
            offer_type=str(facts["offer_type"]),
            requires_card=facts.get("requires_card"),
            has_paid_dependencies=facts.get("has_paid_dependencies"),
            exhaustion_behaviours=(str(facts["exhaustion_behaviour"]),),
        )
    )


def test_hobby_and_blob_safe_stop_offers_are_explainable_z0() -> None:
    for index in range(6):
        facts = load_case(PROVIDER, ADAPTER, "vercel-hobby-plan").expected_candidates[index][
            "facts"
        ]
        assert facts["notes"] == "Personal, non-commercial use only"
        result = _classification("vercel-hobby-plan", index)
        assert result.zero_cost_class == "Z0_TRUE_FREE"
        assert result.reasons
    assert _classification("vercel-blob-pricing").zero_cost_class == "Z0_TRUE_FREE"


def test_the_14_day_pro_trial_is_z2_even_without_a_card() -> None:
    facts = load_case(PROVIDER, ADAPTER, "vercel-pro-trial").expected_candidates[0]["facts"]
    assert facts["requires_card"] is False
    assert facts["trial_length_days"] == "14"
    result = _classification("vercel-pro-trial")
    assert result.zero_cost_class == "Z2_TEMPORARY_OR_CONDITIONAL"
    assert any("temporary" in condition for condition in result.blocking_conditions)


def test_queues_and_the_ai_promotion_do_not_fail_open_to_z0() -> None:
    assert _classification("vercel-queues-pricing").zero_cost_class == "UNKNOWN"
    assert _classification("vercel-ai-gateway-promotion").zero_cost_class == "UNKNOWN"


def test_tiny_identity_and_exact_promotion_wording_share_the_canonical_source() -> None:
    canonical_url = "https://vercel.com/changelog/ling-3-0-tiny-is-now-available-on-ai-gateway"
    fixture = load_case(PROVIDER, ADAPTER, "vercel-ai-gateway-promotion")
    assert fixture.source_url == canonical_url
    (expected,) = fixture.expected_candidates
    assert expected["evidence_url"] == canonical_url
    assert expected["facts"] == {
        "service": "Ling 3.0 Tiny via Vercel AI Gateway",
        "offer_type": "trial",
        "requires_card": None,
        "has_paid_dependencies": None,
        "model_identifier": "inclusionai/ling-3.0-tiny-free",
        "source_published_date": "August 6, 2026",
        "promotion_wording": "free to use till 8:00am PT on 8/14",
        "exhaustion_behaviour": "unknown",
    }
    _, candidates = run_extraction_case(
        PROVIDER,
        ADAPTER,
        "vercel-ai-gateway-promotion",
        official_domains=("vercel.com",),
    )
    (candidate,) = candidates
    assert candidate.source_url == canonical_url
    assert {location.url for location in candidate.evidence} == {canonical_url}
    assert candidate.facts == expected["facts"]


def test_official_post_trial_paths_create_opposite_non_z0_verdicts() -> None:
    assert _classification("contradictory", 0).zero_cost_class == "Z2_TEMPORARY_OR_CONDITIONAL"
    assert _classification("contradictory", 1).zero_cost_class == "Z1_BILLING_EXPOSURE"


def test_mutation_renaming_offer_type_withholds_every_hobby_row() -> None:
    """Prediction: renaming the shared header makes all six rows invalid.

    This probes a page-wide schema drift axis rather than deleting one cell.
    The expected actual result is six ``offer_type=None`` candidates and six
    validation failures; no fallback header or case-normalisation is allowed.
    """

    fixture = load_case(PROVIDER, ADAPTER, "vercel-hobby-plan")
    text = fixture.source_path.read_text(encoding="utf-8")
    mutated = text.replace("<th>Offer type</th>", "<th>Plan kind</th>", 1)
    adapter = build_fixture_adapter(
        fixture, official_domains=("vercel.com",), body=mutated.encode("utf-8")
    )
    candidates = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert len(candidates) == 6
    assert all(candidate.facts["offer_type"] is None for candidate in candidates)
    assert all(adapter.validate(candidate) for candidate in candidates)


def test_complete_fourteen_category_coverage_and_q9a_floor(config) -> None:
    assert set(config.coverage) == CANONICAL_CATEGORIES
    backed = {
        slug: entry
        for slug, entry in config.coverage.items()
        if entry.state in ("verified_free", "offered_no_z0")
    }
    assert len(backed) >= 3
    assert config.coverage["containers-app-hosting"].state == "verified_free"
    assert config.coverage["serverless-functions"].state == "verified_free"
    assert config.coverage["networking-cdn-dns"].state == "verified_free"
    assert all(entry.source or entry.evidence_url for entry in backed.values())


def test_every_absence_has_a_rationale_and_every_source_is_official(config) -> None:
    for entry in config.coverage.values():
        if entry.state == "not_offered":
            assert entry.rationale and entry.rationale.strip()
    for source in config.sources:
        assert source.trust_level == "official"
        assert source.url and source.url.startswith("https://vercel.com/")


def test_every_extracted_service_is_mapped_to_a_declared_published_category(config) -> None:
    for case in OFFICIAL_CASES:
        fixture = load_case(PROVIDER, ADAPTER, case)
        for candidate in fixture.expected_candidates:
            service = candidate["facts"].get("service")
            if not service:
                continue
            assert service in config.service_categories
            slug = config.service_categories[service]
            assert slug in CANONICAL_CATEGORIES
            if candidate.get("expect_valid", True):
                assert config.coverage[slug].state in ("verified_free", "offered_no_z0")


def test_every_assignment_has_a_preceding_rationale_comment() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    block = text.split("service_categories:", 1)[1].split("\ncoverage:", 1)[0]
    lines = [line for line in block.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or ":" not in stripped:
            continue
        previous = [candidate.strip() for candidate in lines[:index] if candidate.strip()]
        assert previous[-1].startswith("#"), stripped
