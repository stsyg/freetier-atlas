"""Offline integrity and safety controls for the Google Cloud provider slice.

The controls here exist to stop one specific failure: publishing a Google Cloud
offer as free when the official pages do not support it. Google Cloud is the
provider most able to produce that failure, because its perpetual Free Tier sits
inside a metered billing account whose overage bills automatically, and because
its Free Trial is credit-backed, time-limited and card-gated while being
published on the SAME page as the perpetual tier.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.classify.engine import OfferFacts, classify
from app.config import load_and_validate
from app.ingest import resolve_profile
from app.ingest.adapters.html import _DocumentCollector, _header_row

from tests.support.fixtures import (
    available_cases,
    build_fixture_adapter,
    load_case,
    run_extraction_case,
)

# One fixed moment for this module's clock-taking calls. The production
# functions require a clock rather than inventing one, so a test must state
# the instant it is asserting about.
_CLOCK = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
_CLOCK_DATE = _CLOCK.date()

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "gcp.example.yaml"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "ingest" / "gcp" / "html"
DOMAINS = ("cloud.google.com",)

SOURCE_CASES = (
    "gcp-free-tier-products",
    "gcp-free-trial",
    "gcp-firestore-free-tier",
    "gcp-bigquery-free-tier",
)
MATRIX_CASES = ("gcp-free-tier-products", "gcp-firestore-free-tier", "gcp-bigquery-free-tier")
PROFILE_NAMES = (
    "gcp_free_tier_products",
    "gcp_free_trial",
    "gcp_firestore_free_tier",
    "gcp_bigquery_free_tier",
)


@pytest.mark.parametrize("case", available_cases("gcp", "html"))
def test_every_gcp_fixture_extracts_exactly_as_declared(case: str) -> None:
    run_extraction_case("gcp", "html", case, official_domains=DOMAINS)


def test_the_five_document_cases_and_four_official_sources_are_present() -> None:
    cases = set(available_cases("gcp", "html"))
    assert {"unchanged", "changed", "partial", "malformed", "contradictory"} <= cases
    assert set(SOURCE_CASES) <= cases


@pytest.mark.parametrize("case", MATRIX_CASES)
def test_capture_structure_matches_every_retained_target_cell(case: str) -> None:
    fixture = load_case("gcp", "html", case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    profile = resolve_profile(fixture.profile)

    matches = []
    for table in collector.tables:
        header_index, header = _header_row(table)
        if header_index is None:
            continue
        normalized = {cell.strip().lower() for cell in header.cells}
        if set(profile.header_signature) <= normalized:
            matches.append((table, header_index, header))
    assert len(matches) == 1
    table, header_index, header = matches[0]
    assert list(header.cells) == capture["structure"]["headers"]
    assert [list(row.cells) for row in table.rows[header_index + 1 :]] == capture["structure"][
        "rows"
    ]
    # ADDITION and OMISSION both break a fixture, so both are declared, and both
    # are declared EMPTY here rather than being left unstated.
    assert capture["target_table_rows_removed"] == []
    assert capture["target_table_cells_removed"] == []
    assert capture["ignored_extraction_rows"] == []
    assert capture["duplicate_live_blocks_not_retained"] == []


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_asserted_blocks_match_the_pinned_capture_hashes(case: str) -> None:
    fixture = load_case("gcp", "html", case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    profile = resolve_profile(fixture.profile)
    actual = [
        hashlib.sha256(assertion.text.encode("utf-8")).hexdigest()
        for assertion in profile.assertions
    ]
    assert actual == capture["structure"]["asserted_block_sha256"]


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_every_pinned_block_occurs_exactly_once_in_its_capture(case: str) -> None:
    """A pinned block that appears twice yields ambiguous_assertion, not a fact."""

    fixture = load_case("gcp", "html", case)
    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    profile = resolve_profile(fixture.profile)

    for index, assertion in enumerate(profile.assertions):
        occurrences = sum(
            1
            for block in collector.text_blocks
            if block.scope == assertion.scope and " ".join(block.text.split()) == assertion.text
        )
        assert occurrences == 1, f"{case}: assertion[{index}] occurs {occurrences} times"


def test_the_free_trial_profile_reads_no_table_and_none_was_synthesized() -> None:
    """Assertion-only means assertion-only, in the profile AND in the capture."""

    profile = resolve_profile("gcp_free_trial")
    assert profile.mode == "assertions"
    assert profile.header_signature == ()
    assert profile.table_id is None
    assert profile.table_class is None
    assert profile.matrix_rows == {}

    fixture = load_case("gcp", "html", "gcp-free-trial")
    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    assert collector.tables == []
    assert "<table" not in fixture.content.decode("utf-8").lower()

    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    assert capture["live_table_present"] is False
    # The live document DOES carry tables; the capture must say so rather than
    # implying the page has none.
    assert capture["live_table_count"] == 2


@pytest.mark.parametrize("case", MATRIX_CASES)
def test_matrix_profiles_map_every_live_row_and_ignore_none(case: str) -> None:
    fixture = load_case("gcp", "html", case)
    profile = resolve_profile(fixture.profile)
    assert profile.mode == "matrix"
    assert profile.header_signature
    assert profile.ignored_matrix_rows == ()
    assert all(row.required for row in profile.matrix_rows.values())


def test_requires_card_is_asserted_only_where_a_sentence_states_it() -> None:
    """The card fact is the one that makes Z0 reachable, so it is quoted or absent."""

    carded = {
        name: {a.field: a.value for a in resolve_profile(name).assertions} for name in PROFILE_NAMES
    }
    # Only the trial claims it, and it claims it is REQUIRED -- the unfavourable
    # direction. No Google Cloud profile claims a card is NOT required.
    assert carded["gcp_free_trial"]["requires_card"] is True
    for name in ("gcp_free_tier_products", "gcp_firestore_free_tier", "gcp_bigquery_free_tier"):
        assert "requires_card" not in carded[name]
    assert all("has_paid_dependencies" not in fields for fields in carded.values())


def test_the_free_tier_and_the_free_trial_are_never_conflated() -> None:
    """Same document, two offers: different identity, type and exhaustion."""

    _, (tier,) = run_extraction_case(
        "gcp", "html", "gcp-free-tier-products", official_domains=DOMAINS
    )
    _, (trial,) = run_extraction_case("gcp", "html", "gcp-free-trial", official_domains=DOMAINS)

    assert tier.facts["service"] == "Google Cloud Free Tier"
    assert trial.facts["service"] == "Google Cloud Free Trial"
    assert tier.facts["offer_type"] == "always_free"
    assert trial.facts["offer_type"] == "trial"
    assert tier.facts["exhaustion_behaviour"] == "automatic_billing"
    assert trial.facts["exhaustion_behaviour"] == "manual_upgrade_required"
    # The credit is the trial's substance and belongs to the trial alone.
    assert trial.facts["welcome_credit"] == "$300"
    assert trial.facts["credit_validity_days"] == "90"
    assert trial.facts["trial_length_days"] == "90"
    assert "welcome_credit" not in tier.facts
    assert "trial_length_days" not in tier.facts


def test_the_always_free_tier_is_a_billing_exposure_because_the_page_says_so() -> None:
    """The unfavourable finding, asserted rather than quietly dropped."""

    _, (tier,) = run_extraction_case(
        "gcp", "html", "gcp-free-tier-products", official_domains=DOMAINS
    )
    assert tier.facts["billing_account"] == "required"
    verdict = classify(
        OfferFacts(
            offer_type=str(tier.facts["offer_type"]),
            requires_card=tier.facts.get("requires_card"),
            has_paid_dependencies=tier.facts.get("has_paid_dependencies"),
            exhaustion_behaviours=(str(tier.facts["exhaustion_behaviour"]),),
        ),
        as_of=_CLOCK_DATE,
    )
    assert verdict.zero_cost_class == "Z1_BILLING_EXPOSURE"
    assert any("automatic billing" in reason for reason in verdict.blocking_conditions)


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_no_google_cloud_offer_can_reach_z0(case: str) -> None:
    _, (candidate,) = run_extraction_case("gcp", "html", case, official_domains=DOMAINS)
    verdict = classify(
        OfferFacts(
            offer_type=str(candidate.facts["offer_type"]),
            requires_card=candidate.facts.get("requires_card"),
            has_paid_dependencies=candidate.facts.get("has_paid_dependencies"),
            exhaustion_behaviours=(str(candidate.facts["exhaustion_behaviour"]),),
        ),
        as_of=_CLOCK_DATE,
    )
    assert verdict.zero_cost_class != "Z0_TRUE_FREE"
    assert verdict.blocking_conditions


@pytest.mark.parametrize(
    ("requires_card", "has_paid_dependencies", "expected"),
    [
        (True, False, "Z1_BILLING_EXPOSURE"),
        (None, None, "UNKNOWN"),
        # The hypothetical that matters: even with every billing gate explicitly
        # clear, a `trial` offer type must still withhold Z0. This proves the
        # non-Z0 outcome comes from the offer TYPE and not merely from the card.
        (False, False, "Z2_TEMPORARY_OR_CONDITIONAL"),
    ],
)
def test_a_credit_backed_trial_is_unreachable_from_z0_on_every_path(
    requires_card: bool | None, has_paid_dependencies: bool | None, expected: str
) -> None:
    verdict = classify(
        OfferFacts(
            offer_type="trial",
            requires_card=requires_card,
            has_paid_dependencies=has_paid_dependencies,
            exhaustion_behaviours=("manual_upgrade_required",),
        ),
        as_of=_CLOCK_DATE,
    )
    assert verdict.zero_cost_class == expected
    assert verdict.zero_cost_class != "Z0_TRUE_FREE"


def test_new_customer_credit_is_equally_unreachable_from_z0() -> None:
    """The other vocabulary value for a credit grant must behave identically."""

    verdict = classify(
        OfferFacts(
            offer_type="new_customer_credit",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        ),
        as_of=_CLOCK_DATE,
    )
    assert verdict.zero_cost_class == "Z2_TEMPORARY_OR_CONDITIONAL"


def test_config_sources_categories_and_coverage_are_complete() -> None:
    config = load_and_validate(CONFIG_PATH)
    assert [source.id for source in config.sources] == list(SOURCE_CASES)
    assert {source.extraction_profile for source in config.sources} == set(PROFILE_NAMES)
    assert len(config.coverage) == 14
    assert set(config.service_categories) == {
        "Google Cloud Free Tier",
        "Google Cloud Free Trial",
        "Firestore",
        "BigQuery",
    }
    assert (
        sum(
            entry.state in {"verified_free", "offered_no_z0"}
            and bool(entry.source or entry.evidence_url)
            for entry in config.coverage.values()
        )
        >= 3
    )
    # Nothing may claim a verified free tier: every extracted Google Cloud offer
    # classifies Z1 or UNKNOWN.
    assert all(entry.state != "verified_free" for entry in config.coverage.values())

    text = CONFIG_PATH.read_text(encoding="utf-8")
    block = text.split("service_categories:", 1)[1].split("\ncoverage:", 1)[0]
    lines = [line for line in block.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#") or ":" not in line:
            continue
        assert lines[index - 1].lstrip().startswith("#")


def test_every_declared_service_category_key_is_an_extracted_service_name() -> None:
    """A mapping key that no candidate produces would silently never apply."""

    config = load_and_validate(CONFIG_PATH)
    extracted = set()
    for case in SOURCE_CASES:
        _, (candidate,) = run_extraction_case("gcp", "html", case, official_domains=DOMAINS)
        extracted.add(str(candidate.facts["service"]))
    assert set(config.service_categories) == extracted


def _block_containing(source: str, tag: str, needle: str) -> str:
    """Return the one ``<tag>...</tag>`` block whose text contains ``needle``.

    Anchoring on the BLOCK rather than on a literal line keeps these mutations
    honest across reformatting: the committed fixtures are Prettier-formatted, so
    a hand-written single-line anchor would silently stop matching and the
    mutation would test nothing. The exactly-once assertion is what makes that
    failure loud instead of silent.
    """

    blocks = [
        match.group(0)
        for match in re.finditer(rf"<{tag}>.*?</{tag}>", source, re.S)
        if needle in " ".join(re.sub(r"<[^>]+>", " ", match.group(0)).split())
    ]
    assert len(blocks) == 1, f"anchor {needle!r} matched {len(blocks)} <{tag}> blocks"
    return blocks[0]


def _mutate(source: str, mutation: str) -> str:
    """Apply exactly one named mutation, refusing an ambiguous anchor."""

    def once(old: str, new: str) -> str:
        assert source.count(old) == 1, f"{mutation}: anchor matched {source.count(old)} times"
        return source.replace(old, new)

    pinned = _block_containing(
        source, "p", "Firestore allows exactly one free database per project."
    )
    stored_row = _block_containing(source, "tr", "Stored data")

    if mutation == "assertion_deleted":
        return once(pinned, "")
    if mutation == "assertion_reworded":
        return once(pinned, "<p>Firestore allows precisely one free database per project.</p>")
    if mutation == "assertion_truncated":
        return once(pinned, "<p>Firestore allows exactly one free database</p>")
    if mutation == "assertion_duplicated":
        return once(pinned, pinned + "\n    " + pinned)
    if mutation == "undeclared_matrix_row":
        return once(
            stored_row,
            stored_row + "\n        <tr><td>Bundled index entries</td><td>1 GiB</td></tr>",
        )
    if mutation == "mapped_row_removed":
        return once(stored_row, "")
    if mutation == "renamed_tier":
        return once('<th scope="col">Quota</th>', '<th scope="col">Quotas</th>')
    if mutation == "duplicated_table":
        table = source.split("<table>", 1)[1].split("</table>", 1)[0]
        return once("</table>", f"</table>\n    <table>{table}</table>")
    if mutation == "extra_column":
        # Only the body rows gain a cell, so the header keeps its real labels and
        # every row stays the same width.
        head, body = source.split("<tbody>", 1)
        head = head.replace(
            '<th scope="col">Quota</th>', '<th scope="col">Quota</th><th scope="col">Notes</th>'
        )
        return head + "<tbody>" + body.replace("</tr>", "<td>x</td></tr>")
    if mutation == "whitespace_entities":
        return once("<td>Stored data</td>", "<td>  Stored&nbsp;data  </td>")
    raise AssertionError(f"unknown mutation {mutation!r}")


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        # Predictions are recorded HERE, in the parametrization, before the
        # mutation runs. Each assertion mutation attacks the same pinned
        # sentence in a different way, because whole-block normalised equality
        # has to reject all three or it protects nothing.
        ("assertion_deleted", "assertion_not_found"),
        ("assertion_reworded", "assertion_not_found"),
        ("assertion_truncated", "assertion_not_found"),
        ("assertion_duplicated", "ambiguous_assertion"),
        ("undeclared_matrix_row", "unknown_matrix_rows"),
        ("mapped_row_removed", "missing_matrix_rows"),
        ("renamed_tier", "table_not_found"),
        ("duplicated_table", "ambiguous_table"),
        # FALSE-POSITIVE CONTROLS. A guard that rejects these would be loosened
        # by the next author, which is worse than no guard.
        ("extra_column", None),
        ("whitespace_entities", None),
    ],
)
def test_predicted_mutations_match_observation(mutation: str, expected_error: str | None) -> None:
    fixture = load_case("gcp", "html", "gcp-firestore-free-tier")
    source = fixture.source_path.read_text(encoding="utf-8")
    adapter = build_fixture_adapter(
        fixture, official_domains=DOMAINS, body=_mutate(source, mutation).encode()
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts.get("error") == expected_error


def test_an_undeclared_product_row_rejects_the_program_page_too() -> None:
    """The 29-row program table is where a new Google product would appear."""

    fixture = load_case("gcp", "html", "gcp-free-tier-products")
    source = fixture.source_path.read_text(encoding="utf-8")
    anchor = _block_containing(source, "tr", "App Engine")
    mutated = source.replace(
        anchor, "<tr><td>Some Future Product</td><td>1 GiB per month</td></tr>\n        " + anchor
    )
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "unknown_matrix_rows"
    assert "Some Future Product" in candidate.facts["detail"]


def test_deleting_the_overage_sentence_rejects_rather_than_publishing_a_free_claim() -> None:
    """The sentence that makes this offer NON-free is load-bearing in both directions."""

    fixture = load_case("gcp", "html", "gcp-free-tier-products")
    source = fixture.source_path.read_text(encoding="utf-8")
    anchor = _block_containing(
        source,
        "p",
        "Any usage that exceeds the Free Tier usage limits is billed at standard rates.",
    )
    mutated = source.replace(anchor, "")
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    # Without its exhaustion evidence the document is REJECTED. It does not fall
    # back to a candidate whose exhaustion is merely unknown, which a downstream
    # reader could mistake for a safe stop.
    assert candidate.facts["error"] == "assertion_not_found"


def test_the_healthy_document_still_extracts_after_the_mutation_battery() -> None:
    """Collateral control: the mutations above proved nothing if this now fails."""

    _, (candidate,) = run_extraction_case(
        "gcp", "html", "gcp-firestore-free-tier", official_domains=DOMAINS
    )
    assert candidate.verification_state == "candidate"
    assert candidate.facts["stored_data"] == "1 GiB"
    assert candidate.facts["offer_type"] == "recurring_quota"
    assert len(candidate.evidence) == 12


def test_captures_declare_synthetic_provenance_where_it_applies() -> None:
    for case in ("unchanged", "changed", "partial", "malformed", "contradictory"):
        capture = json.loads((FIXTURE_ROOT / case / "capture.json").read_text(encoding="utf-8"))
        assert capture["synthetic"] is True
        assert capture["sha256_original"] is None
        assert "NEGATIVE TEST FIXTURE" in capture["negative_fixture_note"]
        assert capture["mutation"]
    for case in SOURCE_CASES:
        capture = json.loads((FIXTURE_ROOT / case / "capture.json").read_text(encoding="utf-8"))
        assert "synthetic" not in capture
        assert capture["http_status"] == 200
        assert capture["sha256_original"]
        assert capture["robots_allowed"] is True
        assert "not reproducible across fetches" in capture["sha256_original_note"].lower()
