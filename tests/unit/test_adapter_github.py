"""Contract tests for the GitHub OFFICIAL free-tier slice (F008 P1).

Offline, fixture-driven, no database, no network. These tests exist to make one
specific failure mode impossible to ship: publishing a **time-limited offer as
though it were perpetually free**.

The three controls that matter here are:

1. :func:`test_every_published_number_appears_verbatim_in_the_captured_source`
   -- traceability. A number that is not present byte-for-byte in the captured
   official excerpt cannot be published, so no allowance can drift in from
   training data or from an editor's memory.
2. :func:`test_the_enterprise_trial_is_not_z0_despite_requiring_no_card` -- the
   headline trap. The GitHub Enterprise Cloud trial asks for **no payment
   method** and still must never be Z0, because it expires after 30 days.
3. :func:`test_a_missing_material_condition_is_unknown_never_assumed_free` --
   unknown is better than guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.classify.engine import OfferFacts, classify
from app.config import load_and_validate
from app.ingest import CandidateFacts, resolve_profile

from tests.support.fixtures import (
    available_cases,
    build_fixture_adapter,
    load_case,
    run_extraction_case,
)

PROVIDER = "github"
ADAPTER = "html"
CONFIG = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "examples"
    / "providers"
    / "github.example.yaml"
)

#: The fourteen canonical category slugs, in the order the brief declares them.
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

#: The captured official sources (fixture dirs named after their source id).
OFFICIAL_CASES = (
    "github-actions-billing",
    "github-packages-billing",
    "github-codespaces-billing",
    "github-pages-limits",
    "github-enterprise-cloud-trial",
)

#: Fact keys that are identity or material-condition metadata, not quotas.
_NON_QUOTA_KEYS = frozenset(
    {"service", "offer_type", "requires_card", "has_paid_dependencies", "exhaustion_behaviour"}
)


@pytest.fixture(scope="module")
def config():
    return load_and_validate(CONFIG)


# --- Extraction ------------------------------------------------------------


def test_the_github_corpus_covers_every_official_source_and_the_case_vocabulary() -> None:
    cases = available_cases(PROVIDER, ADAPTER)
    assert set(OFFICIAL_CASES) <= set(cases), "an official source lost its captured fixture"
    # The five single-document shapes of the seven-case vocabulary; `withdrawn`
    # and `stale` only exist across time and are driven in the integration suite.
    assert {"unchanged", "changed", "partial", "malformed", "contradictory"} <= set(cases)


@pytest.mark.parametrize("case", available_cases(PROVIDER, ADAPTER))
def test_every_github_fixture_case_extracts_exactly_as_captured(case: str) -> None:
    """Facts, evidence, validation and hash stability, for every case."""

    run_extraction_case(PROVIDER, ADAPTER, case, official_domains=("docs.github.com",))


@pytest.mark.parametrize("case", OFFICIAL_CASES)
def test_every_published_number_appears_verbatim_in_the_captured_source(case: str) -> None:
    """No published allowance may exist that the captured page does not state.

    This is the traceability control from the product's first rule: every
    published number must appear verbatim in the captured official excerpt. It
    would fail loudly if anyone hand-edited ``expected.json`` to a remembered
    value without re-capturing the page.
    """

    fixture = load_case(PROVIDER, ADAPTER, case)
    source_text = fixture.source_path.read_text(encoding="utf-8")

    checked = 0
    for candidate in fixture.expected_candidates:
        for key, value in candidate["facts"].items():
            if key in _NON_QUOTA_KEYS or not isinstance(value, str):
                continue
            if not any(ch.isdigit() for ch in value):
                continue
            assert value in source_text, (
                f"{case}: published value {value!r} for {key!r} is not present verbatim "
                f"in {fixture.source_path.name} -- it cannot be traced to the official page."
            )
            checked += 1
    assert checked >= 1, f"{case}: no numeric allowance was checked; the control is vacuous"


def test_a_missing_material_condition_is_unknown_never_assumed_free() -> None:
    """The `partial` case drops `Offer type`: UNKNOWN, and invalid -- not free.

    A page that stops publishing whether an offer is perpetual must never be
    read as "perpetual by default". Extraction records ``None`` and validation
    rejects the candidate, so it can never reach the publisher.
    """

    fixture, candidates = run_extraction_case(
        PROVIDER, ADAPTER, "partial", official_domains=("docs.github.com",)
    )
    (candidate,) = candidates
    assert candidate.facts["offer_type"] is None
    # The real allowance is still read correctly -- only the unknown is unknown.
    assert candidate.facts["minutes_per_month"] == "2,000"


@pytest.mark.parametrize("case", OFFICIAL_CASES)
def test_dropping_a_column_yields_unknown_and_never_a_guessed_number(case: str) -> None:
    """Mutation: delete a mapped column and prove no value is invented."""

    fixture = load_case(PROVIDER, ADAPTER, case)
    text = fixture.source_path.read_text(encoding="utf-8")
    want = fixture.expected_candidates[0]["facts"]

    target = next(
        key for key, value in want.items() if key not in _NON_QUOTA_KEYS and isinstance(value, str)
    )
    header = " ".join(
        word.capitalize() if i == 0 else word for i, word in enumerate(target.split("_"))
    )
    mutated = text.replace(f"<th>{header}</th>\n", "", 1).replace(
        f"<td>{want[target]}</td>\n", "", 1
    )
    assert mutated != text, f"{case}: mutation did not change the document ({header})"

    adapter = build_fixture_adapter(
        fixture, official_domains=("docs.github.com",), body=mutated.encode("utf-8")
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert isinstance(candidate, CandidateFacts)
    assert candidate.facts[target] is None, (
        f"{case}: {target} was invented rather than left unknown"
    )
    assert candidate.facts["service"] == want["service"]


# --- Profiles are data -----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "github_actions_billing",
        "github_packages_billing",
        "github_codespaces_billing",
        "github_pages_limits",
        "github_enterprise_cloud_trial",
    ],
)
def test_github_profiles_are_registered_declaratively(name: str) -> None:
    profile = resolve_profile(name)
    assert profile.name == name
    assert "service" in profile.columns
    assert profile.required_fields == ("service", "offer_type")


# --- Z0: the trap that matters most ----------------------------------------


def _facts_of(case: str) -> OfferFacts:
    fixture = load_case(PROVIDER, ADAPTER, case)
    facts = dict(fixture.expected_candidates[0]["facts"])
    return OfferFacts(
        offer_type=str(facts["offer_type"]),
        requires_card=facts["requires_card"],
        has_paid_dependencies=facts["has_paid_dependencies"],
        exhaustion_behaviours=(facts["exhaustion_behaviour"],),
    )


@pytest.mark.parametrize(
    "case",
    [
        "github-actions-billing",
        "github-packages-billing",
        "github-codespaces-billing",
        "github-pages-limits",
    ],
)
def test_the_perpetual_github_allowances_are_z0_and_explainable(case: str) -> None:
    result = classify(_facts_of(case))
    assert result.zero_cost_class == "Z0_TRUE_FREE"
    assert result.blocking_conditions == ()
    assert result.reasons, "a Z0 verdict with no stated reason is not explainable"


def test_the_enterprise_trial_is_not_z0_despite_requiring_no_card() -> None:
    """THE headline control: no card required, and still not free-forever.

    GitHub's own trial page says verbatim "You do not need to provide a payment
    method to start a trial" and, in the same breath, "The trial lasts for 30
    days". Publishing that as Z0 would be exactly the unsupported free claim the
    product forbids.
    """

    facts = _facts_of("github-enterprise-cloud-trial")
    assert facts.requires_card is False, "the fixture must keep the no-card fact"
    assert facts.has_paid_dependencies is False, "and no paid dependency either"

    result = classify(facts)
    assert result.zero_cost_class != "Z0_TRUE_FREE"
    assert result.zero_cost_class == "Z2_TEMPORARY_OR_CONDITIONAL"
    assert any("temporary" in reason for reason in result.blocking_conditions)


def test_time_limitation_alone_blocks_z0_even_with_a_safe_exhaustion_behaviour() -> None:
    """Isolate the variable: only `offer_type` differs from the Z0 Actions row.

    Without this, the trial's Z2 verdict could be credited entirely to its
    `manual_upgrade_required` exhaustion behaviour -- a control that agrees with
    the hypothesis for the wrong reason.
    """

    trial_but_safe = OfferFacts(
        offer_type="trial",
        requires_card=False,
        has_paid_dependencies=False,
        exhaustion_behaviours=("hard_stop",),
    )
    assert classify(trial_but_safe).zero_cost_class == "Z2_TEMPORARY_OR_CONDITIONAL"

    # The identical facts with a perpetual offer type DO reach Z0, proving the
    # difference is the time limit and nothing else.
    perpetual = OfferFacts(
        offer_type="always_free",
        requires_card=False,
        has_paid_dependencies=False,
        exhaustion_behaviours=("hard_stop",),
    )
    assert classify(perpetual).zero_cost_class == "Z0_TRUE_FREE"


def test_the_contradictory_page_offers_a_row_that_must_never_be_z0() -> None:
    """The synthetic conflicting row bills automatically -> Z1, never Z0."""

    fixture = load_case(PROVIDER, ADAPTER, "contradictory")
    second = dict(fixture.expected_candidates[1]["facts"])
    result = classify(
        OfferFacts(
            offer_type=str(second["offer_type"]),
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=(second["exhaustion_behaviour"],),
        )
    )
    assert result.zero_cost_class == "Z1_BILLING_EXPOSURE"


# --- Coverage declaration (Q9-A / Q10-A) -----------------------------------


def test_all_fourteen_categories_are_declared(config) -> None:
    assert set(config.coverage) == CANONICAL_CATEGORIES


def test_the_q9a_floor_is_met_by_official_sources(config) -> None:
    declared_sources = {s.id for s in config.sources}
    backed = {
        slug: entry
        for slug, entry in config.coverage.items()
        if entry.state in ("verified_free", "offered_no_z0")
    }
    assert len(backed) >= 3, "Q9-A requires at least three evidence-backed categories"
    for slug, entry in backed.items():
        assert entry.source or entry.evidence_url, f"{slug}: free claim with no evidence"
        if entry.source:
            assert entry.source in declared_sources, f"{slug}: undeclared source {entry.source}"
        if entry.evidence_url:
            assert entry.evidence_url.startswith("https://docs.github.com/"), (
                f"{slug}: free claims must cite an OFFICIAL GitHub source"
            )


def test_every_absence_claim_carries_a_rationale(config) -> None:
    for slug, entry in config.coverage.items():
        if entry.state == "not_offered":
            assert entry.rationale and entry.rationale.strip(), (
                f"{slug}: asserting absence is a claim and needs a stated reason"
            )


def test_no_published_category_is_left_undeclared(config) -> None:
    """Q9-A derived-contradiction rule, checked without a database.

    Every category this slice publishes an offer into must be declared
    verified_free or offered_no_z0 -- never unknown, never not_offered.
    """

    published = {
        config.service_categories[service]
        for case in OFFICIAL_CASES
        for service in [
            load_case(PROVIDER, ADAPTER, case).expected_candidates[0]["facts"]["service"]
        ]
    }
    assert published, "the control is vacuous if nothing is published"
    for slug in published:
        assert config.coverage[slug].state in ("verified_free", "offered_no_z0"), (
            f"{slug}: an offer is published here, so the declaration cannot be "
            f"{config.coverage[slug].state!r}"
        )


def test_every_extracted_service_has_a_declared_category(config) -> None:
    """Q10-A: assignment is declared metadata; nothing is inferred."""

    for case in OFFICIAL_CASES:
        service = load_case(PROVIDER, ADAPTER, case).expected_candidates[0]["facts"]["service"]
        assert service in config.service_categories, f"{service}: unassigned category"
        assert config.service_categories[service] in CANONICAL_CATEGORIES


def test_every_category_assignment_carries_a_rationale_comment() -> None:
    """Q10-A requires a ONE-LINE RATIONALE in the YAML, not a bare mapping.

    Parsed YAML drops comments, so this reads the file text and asserts each
    assignment is preceded by prose.
    """

    text = CONFIG.read_text(encoding="utf-8")
    block = text.split("service_categories:", 1)[1].split("\ncoverage:", 1)[0]
    lines = [line for line in block.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or ":" not in stripped:
            continue
        previous = [candidate.strip() for candidate in lines[:index] if candidate.strip()]
        assert previous and previous[-1].startswith("#"), (
            f"assignment {stripped!r} has no rationale comment above it (Q10-A)"
        )


def test_all_sources_are_official_and_on_the_provider_domain(config) -> None:
    for source in config.sources:
        assert source.trust_level == "official"
        assert source.url and source.url.startswith("https://docs.github.com/")
