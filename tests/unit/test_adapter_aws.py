"""Offline integrity and safety controls for the AWS provider slice.

The controls here exist to stop one specific failure: publishing an AWS offer as
free when the official pages do not support it. AWS is the provider most able to
produce that failure, because it markets THREE different free-offer kinds --
perpetual "Always Free" offers, a time-limited introductory tier, and short-term
trials and credits -- under the single brand "AWS Free Tier", and only the first
could ever be perpetual.

The tests below therefore check three separate things, and it matters that they
are separate:

* that every fact traces to a real live table row or a verbatim prose block;
* that a drifted, reworded, truncated or duplicated block REJECTS the document
  rather than publishing a stale value;
* that the REAL classifier withholds Z0 from every AWS offer -- with a POSITIVE
  CONTROL proving the same sweep can observe a Z0, because a zero count from a
  sweep that can never see one proves nothing at all.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest
from app.classify.engine import OfferFacts, classify
from app.config import load_and_validate
from app.ingest import resolve_profile
from app.ingest.adapters.html import (
    HtmlDocAdapter,
    HtmlExtractionProfile,
    HtmlTextAssertion,
    _DocumentCollector,
    _header_row,
)
from app.ingest.fetch import FetchPolicy, FixtureFetcher

from tests.support.fixtures import (
    available_cases,
    build_fixture_adapter,
    load_case,
    run_extraction_case,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "aws.example.yaml"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "ingest" / "aws" / "html"
DOMAINS = ("aws.amazon.com",)

SOURCE_CASES = (
    "aws-free-tier-plan",
    "aws-free-plan",
    "aws-12-month-free-tier",
    "aws-dynamodb-free-tier",
    "aws-api-gateway-free-tier",
    "aws-step-functions-free-tier",
)
DOCUMENT_CASES = ("unchanged", "changed", "partial", "malformed", "contradictory")
MATRIX_CASES = ("aws-free-tier-plan",)
ASSERTION_ONLY_CASES = (
    "aws-free-plan",
    "aws-12-month-free-tier",
    "aws-dynamodb-free-tier",
    "aws-api-gateway-free-tier",
    "aws-step-functions-free-tier",
)
PROFILE_NAMES = (
    "aws_free_tier_plan",
    "aws_free_plan",
    "aws_12_month_free_tier",
    "aws_dynamodb_free_tier",
    "aws_api_gateway_free_tier",
    "aws_step_functions_free_tier",
)

#: A block AWS publishes TWICE on the Free Tier Terms page -- once in the current
#: terms and once in the Legacy terms. MEASURED live. It is deliberately NOT
#: pinned by any profile, and the test below proves that decision was necessary
#: rather than stylistic.
DUPLICATED_TERMS_BLOCK = (
    "If you have not used the AWS resources provided under an Offer during the previous 3 "
    "months, we may reclaim those AWS resources after giving you 30 days\u2019 notice. Even if "
    "your AWS resources are reclaimed, you may continue to participate in Offers using new AWS "
    "resources."
)


@pytest.mark.parametrize("case", available_cases("aws", "html"))
def test_every_aws_fixture_extracts_exactly_as_declared(case: str) -> None:
    run_extraction_case("aws", "html", case, official_domains=DOMAINS)


def test_the_five_document_cases_and_six_official_sources_are_present() -> None:
    """NON-VACUITY GUARD for the parametrised sweep above.

    This test is deliberately NOT parametrised over ``available_cases``: a guard
    that shares the discovery mechanism it guards is decoration, because an
    empty discovery would silently collapse the sweep to zero cases AND satisfy
    the guard at the same time. The expected names are written out literally.
    """

    cases = set(available_cases("aws", "html"))
    assert set(DOCUMENT_CASES) <= cases
    assert set(SOURCE_CASES) <= cases
    assert len(SOURCE_CASES) == 6
    assert len(cases) >= len(SOURCE_CASES) + len(DOCUMENT_CASES)
    # Every declared case must exist on disk, independently of discovery.
    for case in SOURCE_CASES + DOCUMENT_CASES:
        assert (FIXTURE_ROOT / case / "expected.json").is_file()


@pytest.mark.parametrize("case", MATRIX_CASES)
def test_capture_structure_matches_every_retained_target_cell(case: str) -> None:
    fixture = load_case("aws", "html", case)
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
    # The one column this profile does NOT read is declared, so the omission is
    # disclosed rather than silent.
    assert capture["unpivoted_target_table_columns"] == ["Paid plan"]


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_asserted_blocks_match_the_pinned_capture_hashes(case: str) -> None:
    fixture = load_case("aws", "html", case)
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

    fixture = load_case("aws", "html", case)
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


@pytest.mark.parametrize("case", ASSERTION_ONLY_CASES)
def test_assertion_only_profiles_read_no_table_and_none_was_synthesized(case: str) -> None:
    """Assertion-only means assertion-only, in the profile AND in the capture."""

    fixture = load_case("aws", "html", case)
    profile = resolve_profile(fixture.profile)
    assert profile.mode == "assertions"
    assert profile.header_signature == ()
    assert profile.table_id is None
    assert profile.table_class is None
    assert profile.matrix_rows == {}

    document = fixture.content.decode("utf-8")
    collector = _DocumentCollector()
    collector.feed(document)
    collector.close()
    assert collector.tables == []
    assert "<table" not in document.lower()

    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    assert capture["live_table_present"] is False
    # Where the LIVE page does carry tables, the capture must say so rather than
    # implying the page has none.
    assert isinstance(capture["live_table_count"], int)
    assert "ASSERTION-ONLY" in capture["live_table_note"]


def test_the_matrix_profile_maps_every_live_row_and_ignores_none() -> None:
    profile = resolve_profile("aws_free_tier_plan")
    assert profile.mode == "matrix"
    assert profile.header_signature == ("benefits", "free plan", "paid plan")
    assert profile.ignored_matrix_rows == ()
    assert len(profile.matrix_rows) == 6
    assert all(row.required for row in profile.matrix_rows.values())


def test_requires_card_is_asserted_only_where_a_single_block_states_it() -> None:
    """The card fact is the one that makes Z0 reachable, so it is quoted or absent."""

    carded = {
        name: {a.field: a.value for a in resolve_profile(name).assertions} for name in PROFILE_NAMES
    }
    # Only the free-plan profile claims it, and it claims a payment method IS
    # required -- the unfavourable direction. That block names the free plan
    # itself ("whether you choose a free plan or a paid plan"), so it is a
    # quotation and not a composition of two blocks.
    assert carded["aws_free_plan"]["requires_card"] is True
    for name in PROFILE_NAMES:
        if name != "aws_free_plan":
            assert "requires_card" not in carded[name], (
                f"{name}: no single block on its document states a payment-method "
                "requirement; carrying the FAQ sentence across documents would be "
                "composition, not quotation."
            )
    # No AWS profile claims a card is NOT required, in either direction.
    assert all(fields.get("requires_card") is not False for fields in carded.values())
    assert all("has_paid_dependencies" not in fields for fields in carded.values())


def test_the_three_aws_offer_kinds_are_never_conflated() -> None:
    """Perpetual, time-limited and credit-backed offers stay distinct."""

    extracted = {}
    for case in SOURCE_CASES:
        _, (candidate,) = run_extraction_case("aws", "html", case, official_domains=DOMAINS)
        extracted[str(candidate.facts["service"])] = dict(candidate.facts)

    # The credit-backed account plans.
    assert extracted["AWS Free Tier"]["offer_type"] == "new_customer_credit"
    assert extracted["AWS Free Tier free plan"]["offer_type"] == "new_customer_credit"
    # The time-limited introductory offers.
    assert extracted["AWS 12 Month Free Tier"]["offer_type"] == "trial"
    assert extracted["Amazon API Gateway"]["offer_type"] == "trial"
    # The monthly replenishing grant on a metered service.
    assert extracted["Amazon DynamoDB"]["offer_type"] == "recurring_quota"
    # The ONE genuinely perpetual offer.
    assert extracted["AWS Step Functions"]["offer_type"] == "always_free"

    # A credit belongs to the account plans and to nothing else.
    assert extracted["AWS Free Tier"]["credit_amount"] == "$200"
    assert "credit_amount" not in extracted["AWS Step Functions"]
    assert "trial_length_months" not in extracted["AWS Free Tier free plan"]
    # The perpetual offer carries no term, and the time-limited ones do.
    assert "trial_length_months" not in extracted["AWS Step Functions"]
    assert extracted["AWS 12 Month Free Tier"]["trial_length_months"] == "12"
    assert extracted["Amazon API Gateway"]["trial_length_months"] == "12"


def test_a_perpetual_aws_offer_is_still_a_billing_exposure() -> None:
    """The single most important finding in this slice.

    Step Functions states its free tier is available "indefinitely" AND that
    usage above it is charged. Perpetual does not mean Z0, and this proves the
    product says so using the real classifier rather than an author's summary.
    """

    _, (candidate,) = run_extraction_case(
        "aws", "html", "aws-step-functions-free-tier", official_domains=DOMAINS
    )
    assert candidate.facts["offer_type"] == "always_free"
    assert "indefinitely" in str(candidate.facts["availability"])
    assert candidate.facts["exhaustion_behaviour"] == "automatic_billing"

    result = classify(
        OfferFacts(
            offer_type=str(candidate.facts["offer_type"]),
            requires_card=candidate.facts.get("requires_card"),
            has_paid_dependencies=candidate.facts.get("has_paid_dependencies"),
            exhaustion_behaviours=(str(candidate.facts["exhaustion_behaviour"]),),
        )
    )
    assert result.zero_cost_class == "Z1_BILLING_EXPOSURE"
    assert any("automatic billing" in reason for reason in result.blocking_conditions)


def test_a_payment_method_is_required_and_that_is_published() -> None:
    """The unfavourable finding, asserted rather than quietly dropped."""

    _, (candidate,) = run_extraction_case("aws", "html", "aws-free-plan", official_domains=DOMAINS)
    assert candidate.facts["requires_card"] is True
    assert "valid payment method" in str(candidate.facts["payment_method_purpose"])
    # The always-free overage answer travels with the offer instead of being
    # discarded, but it is NOT the free plan's exhaustion behaviour.
    assert "begin incurring charges" in str(candidate.facts["overage_note"])
    assert candidate.facts["exhaustion_behaviour"] == "manual_upgrade_required"

    result = classify(
        OfferFacts(
            offer_type=str(candidate.facts["offer_type"]),
            requires_card=candidate.facts.get("requires_card"),
            has_paid_dependencies=candidate.facts.get("has_paid_dependencies"),
            exhaustion_behaviours=(str(candidate.facts["exhaustion_behaviour"]),),
        )
    )
    assert result.zero_cost_class == "Z1_BILLING_EXPOSURE"
    assert any("payment card is required" in reason for reason in result.blocking_conditions)


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_no_aws_offer_can_reach_z0(case: str) -> None:
    _, (candidate,) = run_extraction_case("aws", "html", case, official_domains=DOMAINS)
    behaviours = ()
    if candidate.facts.get("exhaustion_behaviour"):
        behaviours = (str(candidate.facts["exhaustion_behaviour"]),)
    result = classify(
        OfferFacts(
            offer_type=str(candidate.facts["offer_type"]),
            requires_card=candidate.facts.get("requires_card"),
            has_paid_dependencies=candidate.facts.get("has_paid_dependencies"),
            exhaustion_behaviours=behaviours,
        )
    )
    assert result.zero_cost_class != "Z0_TRUE_FREE"
    assert result.blocking_conditions


def test_the_z0_sweep_is_not_vacuous() -> None:
    """POSITIVE CONTROL for the sweep above.

    Without this, "no AWS offer reached Z0" would be indistinguishable from "the
    classifier cannot emit Z0 at all", and the zero count would prove nothing.
    """

    control = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    assert control.zero_cost_class == "Z0_TRUE_FREE"
    assert control.is_zero_cost
    assert not control.blocking_conditions


@pytest.mark.parametrize(
    ("offer_type", "requires_card", "has_paid_dependencies", "expected"),
    [
        # A time-limited offer cannot reach Z0 on ANY path, including the
        # hypothetical where every billing gate is explicitly clear. That proves
        # the non-Z0 outcome comes from the offer TYPE, not merely from the card.
        ("trial", True, False, "Z1_BILLING_EXPOSURE"),
        ("trial", None, None, "UNKNOWN"),
        ("trial", False, False, "Z2_TEMPORARY_OR_CONDITIONAL"),
        # The other vocabulary value for a credit grant must behave identically.
        ("new_customer_credit", True, False, "Z1_BILLING_EXPOSURE"),
        ("new_customer_credit", None, None, "UNKNOWN"),
        ("new_customer_credit", False, False, "Z2_TEMPORARY_OR_CONDITIONAL"),
    ],
)
def test_time_limited_and_credit_backed_offers_are_unreachable_from_z0(
    offer_type: str, requires_card: bool | None, has_paid_dependencies: bool | None, expected: str
) -> None:
    result = classify(
        OfferFacts(
            offer_type=offer_type,
            requires_card=requires_card,
            has_paid_dependencies=has_paid_dependencies,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    assert result.zero_cost_class == expected
    assert result.zero_cost_class != "Z0_TRUE_FREE"


def test_config_sources_categories_and_coverage_are_complete() -> None:
    config = load_and_validate(CONFIG_PATH)
    assert [source.id for source in config.sources] == list(SOURCE_CASES)
    assert {source.extraction_profile for source in config.sources} == set(PROFILE_NAMES)
    assert len(config.coverage) == 14
    assert set(config.service_categories) == {
        "AWS Free Tier",
        "AWS Free Tier free plan",
        "AWS 12 Month Free Tier",
        "Amazon DynamoDB",
        "Amazon API Gateway",
        "AWS Step Functions",
    }
    # Nothing may claim a verified free tier: every extracted AWS offer
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
        _, (candidate,) = run_extraction_case("aws", "html", case, official_domains=DOMAINS)
        extracted.add(str(candidate.facts["service"]))
    assert set(config.service_categories) == extracted


def test_a_block_aws_publishes_twice_would_be_ambiguous_if_pinned() -> None:
    """Proof that declining to pin the duplicated Terms blocks was NECESSARY.

    AWS repeats four clauses verbatim in both the current and the Legacy sections
    of its Free Tier Terms. The capture retains BOTH occurrences so the fixture
    reproduces the live ambiguity rather than hiding it, and this test shows what
    pinning one would actually do.
    """

    fixture = load_case("aws", "html", "aws-12-month-free-tier")
    document = fixture.content.decode("utf-8")
    collector = _DocumentCollector()
    collector.feed(document)
    collector.close()
    occurrences = sum(
        1
        for block in collector.text_blocks
        if block.scope == "document" and " ".join(block.text.split()) == DUPLICATED_TERMS_BLOCK
    )
    assert occurrences == 2, f"expected the live duplication to be preserved, saw {occurrences}"

    greedy = HtmlExtractionProfile(
        name="aws_terms_duplicate_probe",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=DUPLICATED_TERMS_BLOCK,
                field="notes",
                value=DUPLICATED_TERMS_BLOCK,
            ),
        ),
        required_fields=("notes",),
    )
    adapter = HtmlDocAdapter(
        FixtureFetcher(
            {fixture.source_url: (fixture.content, "text/html")},
            FetchPolicy(official_domains=DOMAINS),
        ),
        source_urls=(fixture.source_url,),
        profile=greedy,
        provider="aws",
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "ambiguous_assertion"


# --------------------------------------------------------------------------- #
# Mutation battery                                                            #
# --------------------------------------------------------------------------- #


def _block_containing(source: str, tag: str, needle: str) -> str:
    """Return the one ``<tag>...</tag>`` block whose text contains ``needle``.

    Anchoring on the BLOCK rather than on a literal line keeps these mutations
    honest across reformatting: a hand-written single-line anchor would silently
    stop matching and the mutation would test nothing. The exactly-once
    assertion is what makes that failure loud instead of silent.
    """

    blocks = [
        match.group(0)
        for match in re.finditer(rf"<{tag}[^>]*>.*?</{tag}>", source, re.S)
        if needle in " ".join(re.sub(r"<[^>]+>", " ", match.group(0)).split())
    ]
    assert len(blocks) == 1, f"anchor {needle!r} matched {len(blocks)} <{tag}> blocks"
    return blocks[0]


CREDIT_ANCHOR = "When you create a new AWS Free Tier account"
ROW_ANCHOR = "Promotional Credits"


def _mutate(source: str, mutation: str) -> str:
    """Apply exactly one named mutation, refusing an ambiguous anchor."""

    def once(old: str, new: str) -> str:
        assert source.count(old) == 1, f"{mutation}: anchor matched {source.count(old)} times"
        return source.replace(old, new)

    pinned = _block_containing(source, "p", CREDIT_ANCHOR)
    promo_row = _block_containing(source, "tr", ROW_ANCHOR)

    if mutation == "assertion_deleted":
        return once(pinned, "")
    if mutation == "assertion_reworded":
        return once(pinned, pinned.replace("you get $100 in credits", "you get $150 in credits"))
    if mutation == "assertion_truncated":
        return once(pinned, "<p>When you create a new AWS Free Tier account, you get $100.</p>")
    if mutation == "assertion_duplicated":
        return once(pinned, pinned + "\n    " + pinned)
    if mutation == "undeclared_matrix_row":
        return once(
            promo_row,
            promo_row
            + '\n        <tr><th scope="row">Some Future Benefit</th>'
            + "<td><p>x</p></td><td><p>y</p></td></tr>",
        )
    if mutation == "mapped_row_removed":
        return once(promo_row, "")
    if mutation == "renamed_tier":
        return once('<th scope="col">Free plan</th>', '<th scope="col">Free plans</th>')
    if mutation == "duplicated_table":
        table = source.split("<table>", 1)[1].split("</table>", 1)[0]
        return once("</table>", f"</table>\n    <table>{table}</table>")
    if mutation == "extra_column":
        # Header AND body rows gain a cell, so every row keeps a consistent width
        # and the declared signature is still a subset of the live headers.
        head, body = source.split("<tbody>", 1)
        head = head.replace(
            '<th scope="col">Paid plan</th>',
            '<th scope="col">Paid plan</th><th scope="col">Notes</th>',
        )
        return head + "<tbody>" + body.replace("</tr>", "<td>x</td></tr>")
    if mutation == "whitespace_entities":
        return once(
            '<th scope="row">Promotional Credits</th>',
            '<th scope="row">  Promotional&nbsp;Credits  </th>',
        )
    raise AssertionError(f"unknown mutation {mutation!r}")


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        # Predictions are recorded HERE, in the parametrization, before the
        # mutation runs. Each assertion mutation attacks the same pinned block in
        # a different way, because whole-block normalised equality has to reject
        # all three or it protects nothing.
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
    fixture = load_case("aws", "html", "aws-free-tier-plan")
    source = fixture.source_path.read_text(encoding="utf-8")
    adapter = build_fixture_adapter(
        fixture, official_domains=DOMAINS, body=_mutate(source, mutation).encode()
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts.get("error") == expected_error


def _run_with_profile(case: str, profile: HtmlExtractionProfile, body: bytes):
    fixture = load_case("aws", "html", case)
    adapter = HtmlDocAdapter(
        FixtureFetcher(
            {fixture.source_url: (body, "text/html")}, FetchPolicy(official_domains=DOMAINS)
        ),
        source_urls=(fixture.source_url,),
        profile=profile,
        provider="aws",
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    return candidate


def test_the_assertion_guard_is_what_produces_assertion_not_found() -> None:
    """Mutation test: patch the GUARD, not the document, and prove the error dies.

    Counting red tests proves nothing on its own -- a test can go red for any
    reason. This patches the profile so the required-assertion guard can no
    longer fire, then feeds it the very input that SHOULD raise
    ``assertion_not_found`` and shows the error has become UNPRODUCIBLE.
    """

    fixture = load_case("aws", "html", "aws-free-tier-plan")
    source = fixture.source_path.read_text(encoding="utf-8")
    deleted = _mutate(source, "assertion_deleted").encode()
    profile = resolve_profile("aws_free_tier_plan")

    # Baseline: with the real profile the guard fires on this input.
    baseline = _run_with_profile("aws-free-tier-plan", profile, deleted)
    assert baseline.facts.get("error") == "assertion_not_found"

    # PATCH: every assertion on the deleted block becomes optional. This is the
    # single line of behaviour under test, printed so a reader can see exactly
    # what was disabled.
    patched_assertions = tuple(
        dataclasses.replace(a, required=False) if CREDIT_ANCHOR in a.text else a
        for a in profile.assertions
    )
    patched = dataclasses.replace(profile, assertions=patched_assertions)
    disabled = [a.field for a in patched.assertions if not a.required]
    print(f"PATCHED LINE: HtmlTextAssertion(required=True -> False) for fields {disabled}")
    assert disabled, "the patch must actually disable something"

    # The target error is now UNPRODUCIBLE on an input that should raise it.
    weakened = _run_with_profile("aws-free-tier-plan", patched, deleted)
    assert weakened.facts.get("error") != "assertion_not_found"
    assert weakened.verification_state == "candidate"

    # COLLATERAL CONTROL: a healthy document must still extract under the patched
    # profile. Without this we could not tell "the guard stopped firing" from
    # "something upstream collapsed and nothing extracts at all".
    healthy = _run_with_profile("aws-free-tier-plan", patched, fixture.content)
    assert healthy.verification_state == "candidate"
    assert healthy.facts["service"] == "AWS Free Tier"
    assert healthy.facts["offer_type"] == "new_customer_credit"


def test_the_row_completeness_guard_is_what_produces_unknown_matrix_rows() -> None:
    """Mutation test for the matrix guard, same shape as the assertion one."""

    fixture = load_case("aws", "html", "aws-free-tier-plan")
    source = fixture.source_path.read_text(encoding="utf-8")
    injected = _mutate(source, "undeclared_matrix_row").encode()
    profile = resolve_profile("aws_free_tier_plan")

    baseline = _run_with_profile("aws-free-tier-plan", profile, injected)
    assert baseline.facts.get("error") == "unknown_matrix_rows"
    assert "Some Future Benefit" in baseline.facts["detail"]

    # PATCH: declare the injected row, so completeness can no longer be violated.
    patched_rows = dict(profile.matrix_rows)
    patched_rows["Some Future Benefit"] = next(iter(profile.matrix_rows.values()))
    patched = dataclasses.replace(profile, matrix_rows=patched_rows)
    print(
        "PATCHED LINE: matrix_rows += {'Some Future Benefit': HtmlMatrixRow(...)} "
        f"({len(profile.matrix_rows)} -> {len(patched.matrix_rows)} declared rows)"
    )

    weakened = _run_with_profile("aws-free-tier-plan", patched, injected)
    assert weakened.facts.get("error") != "unknown_matrix_rows"

    # COLLATERAL CONTROL. The patched profile declares a row the healthy document
    # does not contain, so a healthy document must now fail for a DIFFERENT and
    # named reason rather than passing silently or failing for the old one.
    healthy = _run_with_profile("aws-free-tier-plan", patched, fixture.content)
    assert healthy.facts.get("error") == "missing_matrix_rows"


def test_deleting_the_overage_sentence_rejects_rather_than_publishing_a_free_claim() -> None:
    """The sentence that makes the perpetual offer NON-free is load-bearing."""

    fixture = load_case("aws", "html", "aws-step-functions-free-tier")
    source = fixture.source_path.read_text(encoding="utf-8")
    anchor = _block_containing(source, "p", "You are charged per state transition")
    mutated = source.replace(anchor, "")
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    # Without its billing evidence the document is REJECTED. It does NOT fall
    # back to a perpetual offer whose exhaustion is merely unknown, which a
    # downstream reader could mistake for a safe stop -- and which, for an
    # `always_free` offer, is the exact shape of a false $0 claim.
    assert candidate.facts["error"] == "assertion_not_found"


def test_deleting_the_payment_method_sentence_rejects_the_free_plan_document() -> None:
    """Removing the card evidence must not silently produce a cardless offer."""

    fixture = load_case("aws", "html", "aws-free-plan")
    source = fixture.source_path.read_text(encoding="utf-8")
    anchor = _block_containing(source, "p", "you are required to provide a valid payment method")
    mutated = source.replace(anchor, "")
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "assertion_not_found"


def test_the_healthy_documents_still_extract_after_the_mutation_battery() -> None:
    """Collateral control: the mutations above proved nothing if these now fail."""

    _, (plan,) = run_extraction_case("aws", "html", "aws-free-tier-plan", official_domains=DOMAINS)
    assert plan.verification_state == "candidate"
    assert plan.facts["billed_for_the_usage"].startswith("\u2716 No charges incurred")
    assert len(plan.evidence) == 12

    _, (steps,) = run_extraction_case(
        "aws", "html", "aws-step-functions-free-tier", official_domains=DOMAINS
    )
    assert steps.verification_state == "candidate"
    assert steps.facts["free_state_transitions_per_month"] == "4,000"
    assert len(steps.evidence) == 7


def test_captures_declare_synthetic_provenance_where_it_applies() -> None:
    for case in DOCUMENT_CASES:
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
        # sha256_stored is a tamper-evidence seal on the committed bytes, NOT a
        # link to live, and the capture must say so.
        assert "tamper-evidence" in capture["sha256_stored_note"]
        assert "LIVE -> FIXTURE (primary)" in capture["live_reconciliation"]
