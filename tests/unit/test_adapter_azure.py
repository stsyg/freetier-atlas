"""Offline integrity and safety controls for the Microsoft Azure provider slice.

The controls here exist to stop one specific failure: publishing an Azure offer
as free when the official pages do not support it -- and its mirror image,
omitting an Azure offer that the official pages DO support. Azure is unusually
able to produce both, because it markets FOUR different free things under
overlapping branding: a credit-backed *Azure free account*, a *12 months free
services* introductory window, per-service *free plans and free tiers* (one of
which Microsoft calls lifetime), and eligibility-gated *programmes* such as Azure
for Students.

The tests below therefore check four separate things, and it matters that they
are separate:

* that every fact traces to a real live table row or a verbatim prose block;
* that a drifted, reworded, truncated or duplicated block REJECTS the document
  rather than publishing a stale value, while an UNRELATED edit does not, so the
  guards discriminate instead of rejecting any change at all;
* that the REAL classifier withholds Z0 from every Azure offer -- with a POSITIVE
  CONTROL proving the same sweep can observe a Z0, and a further test proving
  that control is LOAD-BEARING by breaking the engine and watching the control
  fail while the sweep sails on;
* that the two favourable findings in this provider -- a genuinely perpetual
  Cosmos DB tier and the only "No credit card required" block in the sweep -- are
  PUBLISHED rather than quietly dropped in the name of caution.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import re
from pathlib import Path

import pytest
from app.classify import engine
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
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "azure.example.yaml"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "ingest" / "azure" / "html"
DOMAINS = ("azure.microsoft.com", "learn.microsoft.com")

SOURCE_CASES = (
    "azure-free-account",
    "azure-free-services",
    "azure-cosmos-db-free-tier",
    "azure-app-service-quotas",
    "azure-static-web-apps-plans",
    "azure-devops-services",
    "azure-students",
)
DOCUMENT_CASES = ("unchanged", "changed", "partial", "malformed", "contradictory")
MATRIX_CASES = ("azure-app-service-quotas", "azure-static-web-apps-plans")
ASSERTION_ONLY_CASES = (
    "azure-free-account",
    "azure-free-services",
    "azure-cosmos-db-free-tier",
    "azure-devops-services",
    "azure-students",
)
PROFILE_NAMES = (
    "azure_free_account",
    "azure_free_services",
    "azure_cosmos_db_free_tier",
    "azure_app_service_quotas",
    "azure_static_web_apps_plans",
    "azure_devops_services",
    "azure_students",
)

#: The block that makes the Azure free account Z1 by quotation.
PAYMENT_METHOD_REQUIRED = (
    "All you need is a phone number, a credit card or a debit card (non-prepaid), and a "
    "Microsoft account or a GitHub account. Only credit cards are accepted in Hong Kong and "
    "Brazil."
)

#: The three reasons the classifier emits at gate 3, its DEFINITE billing gate.
#: Asserting the absence of these is the accurate way to say "clears the billing
#: gate" -- an earlier draft of this module asserted the substring "unknown"
#: appeared in every blocker instead, which is a claim about WORDING rather than
#: about the gate, and it was wrong: gate 4's "No quota data is available to
#: confirm a safe exhaustion behaviour." contains no such word. That draft was
#: caught by this file's own Static Web Apps case.
BILLING_GATE_REASONS = (
    "A payment card is required.",
    "The offer has paid dependencies.",
    "A quota triggers automatic billing when exhausted.",
)

#: The EXACT blocking conditions the real classifier returns for App Service, in
#: sorted order. Written out literally rather than derived, so a change to the
#: engine's wording or to the number of blockers fails loudly here instead of
#: being absorbed by a membership test. This offer is the closest thing in the
#: slice to a Z0 claim, so its blocking set is pinned rather than sampled.
PAID_DEPS_UNKNOWN = "Whether the offer has paid dependencies is unknown."
CARD_UNKNOWN = "Whether a payment card is required is unknown."
APP_SERVICE_BLOCKERS = tuple(sorted((CARD_UNKNOWN, PAID_DEPS_UNKNOWN)))


def _classify(facts) -> engine.ClassificationResult:
    """Classify one extracted candidate exactly as the pipeline would."""

    behaviours = ()
    if facts.get("exhaustion_behaviour"):
        behaviours = (str(facts["exhaustion_behaviour"]),)
    return classify(
        OfferFacts(
            offer_type=str(facts["offer_type"]),
            requires_card=facts.get("requires_card"),
            has_paid_dependencies=facts.get("has_paid_dependencies"),
            exhaustion_behaviours=behaviours,
        )
    )


@pytest.mark.parametrize("case", available_cases("azure", "html"))
def test_every_azure_fixture_extracts_exactly_as_declared(case: str) -> None:
    run_extraction_case("azure", "html", case, official_domains=DOMAINS)


def test_the_five_document_cases_and_seven_official_sources_are_present() -> None:
    """NON-VACUITY GUARD for the parametrised sweep above.

    This test is deliberately NOT parametrised over ``available_cases``: a guard
    that shares the discovery mechanism it guards is decoration, because an
    empty discovery would silently collapse the sweep to zero cases AND satisfy
    the guard at the same time. The expected names are written out literally.
    """

    cases = set(available_cases("azure", "html"))
    assert set(DOCUMENT_CASES) <= cases
    assert set(SOURCE_CASES) <= cases
    assert len(SOURCE_CASES) == 7
    assert len(cases) >= len(SOURCE_CASES) + len(DOCUMENT_CASES)
    # Every declared case must exist on disk, independently of discovery.
    for case in SOURCE_CASES + DOCUMENT_CASES:
        assert (FIXTURE_ROOT / case / "expected.json").is_file()


@pytest.mark.parametrize("case", MATRIX_CASES)
def test_capture_structure_matches_every_retained_target_cell(case: str) -> None:
    fixture = load_case("azure", "html", case)
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


def test_the_unread_static_web_apps_columns_are_declared() -> None:
    """The two columns this slice does NOT read are disclosed, not silent."""

    capture = json.loads(
        (FIXTURE_ROOT / "azure-static-web-apps-plans" / "capture.json").read_text(encoding="utf-8")
    )
    assert capture["unpivoted_target_table_columns"] == [
        "Standard plan (For production apps)",
        "Dedicated plan (Retired effective October 31st, 2025)",
    ]
    # The App Service table has only a metric and a description column, so there
    # is nothing to unpivot there -- declared empty rather than left unstated.
    app_service = json.loads(
        (FIXTURE_ROOT / "azure-app-service-quotas" / "capture.json").read_text(encoding="utf-8")
    )
    assert app_service["unpivoted_target_table_columns"] == []


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_asserted_blocks_match_the_pinned_capture_hashes(case: str) -> None:
    fixture = load_case("azure", "html", case)
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

    fixture = load_case("azure", "html", case)
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


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_every_capture_records_both_reconciliation_directions(case: str) -> None:
    """A one-directional reconciliation claim hides added content.

    The retained-block COUNT is cross-checked against the committed bytes rather
    than taken on trust: a sidecar that claims a number is only evidence if the
    number can be recomputed from the file it describes. RETAINED is deliberately
    distinguished from ASSERTED -- a live ``<title>`` kept for context is a
    retained block that no profile reads -- because conflating the two is exactly
    the error the first revision of these captures made.
    """

    fixture = load_case("azure", "html", case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    reconciliation = capture["live_reconciliation"]
    assert "LIVE -> FIXTURE (primary)" in reconciliation
    assert "FIXTURE -> LIVE (supporting only)" in reconciliation
    assert "none removed, none added" in reconciliation
    assert "RETAINED is not the same as ASSERTED" in reconciliation

    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    retained = len(collector.text_blocks)
    assert capture["retained_live_block_count"] == retained
    assert f"all {retained} retained live block(s)" in reconciliation

    # Every asserted block must be one of the retained ones. That is the link
    # between the sidecar's claim and the profile's evidence.
    profile = resolve_profile(fixture.profile)
    present = {(block.scope, block.text) for block in collector.text_blocks}
    for assertion in profile.assertions:
        assert (assertion.scope, assertion.text) in present

    # Row and cell counts must match the retained target table exactly.
    rows = capture["structure"]["rows"]
    cells = sum(len(row) for row in rows)
    assert f"all {len(rows)} live target-table row(s)" in reconciliation
    assert f"all {cells} target-table cell(s)" in reconciliation


@pytest.mark.parametrize("case", ASSERTION_ONLY_CASES)
def test_assertion_only_profiles_read_no_table_and_none_was_synthesized(case: str) -> None:
    """Assertion-only means assertion-only, in the profile AND in the capture."""

    fixture = load_case("azure", "html", case)
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


def test_the_matrix_profiles_map_every_live_row_and_ignore_none() -> None:
    app_service = resolve_profile("azure_app_service_quotas")
    assert app_service.mode == "matrix"
    assert app_service.header_signature == ("quota", "description")
    assert app_service.ignored_matrix_rows == ()
    assert len(app_service.matrix_rows) == 5
    assert all(row.required for row in app_service.matrix_rows.values())

    static_web_apps = resolve_profile("azure_static_web_apps_plans")
    assert static_web_apps.mode == "matrix"
    assert static_web_apps.header_signature == (
        "feature",
        "free plan (for personal projects)",
        "standard plan (for production apps)",
    )
    assert static_web_apps.ignored_matrix_rows == ()
    assert len(static_web_apps.matrix_rows) == 13
    assert all(row.required for row in static_web_apps.matrix_rows.values())


def test_requires_card_is_asserted_only_where_a_single_block_states_it() -> None:
    """The card fact is the one that makes Z0 reachable, so it is quoted or absent."""

    carded = {
        name: {a.field: a.value for a in resolve_profile(name).assertions} for name in PROFILE_NAMES
    }
    # Only the free-account profile claims it, and it claims a payment method IS
    # required -- the unfavourable direction. That block sits under the "Payment
    # options" heading of a document whose own heading is "Azure free account
    # terms & conditions", so it is a quotation about THAT offer.
    assert carded["azure_free_account"]["requires_card"] is True
    for name in PROFILE_NAMES:
        if name != "azure_free_account":
            assert "requires_card" not in carded[name], (
                f"{name}: no single block on its document states a payment-method "
                "requirement for the offer it describes; carrying the free-account "
                "sentence across documents would be composition, not quotation."
            )
    # No Azure profile claims a card is NOT required, in either direction, and
    # none claims anything at all about paid dependencies.
    assert all(fields.get("requires_card") is not False for fields in carded.values())
    assert all("has_paid_dependencies" not in fields for fields in carded.values())


def test_the_students_card_claim_is_published_but_not_used_as_a_gate() -> None:
    """The one favourable card block in the sweep is published, not dropped.

    ``https://azure.microsoft.com/en-us/free/students/`` is the ONLY page in this
    sweep stating that a card is not required. Omitting it would under-report a
    real free offer, which this product treats as a defect equal to overstating
    one, so it IS extracted. It is deliberately not converted into
    ``requires_card=False`` because the live page carries two offers whose card
    terms point in opposite directions and the bullet names neither.
    """

    _, (candidate,) = run_extraction_case(
        "azure", "html", "azure-students", official_domains=DOMAINS
    )
    assert candidate.facts["card_claim"] == "No credit card required"
    assert "requires_card" not in candidate.facts
    # The capture must disclose the two-offer page and the cross-scope duplicate
    # rather than leaving a reader to discover them.
    capture = json.loads(
        (FIXTURE_ROOT / "azure-students" / "capture.json").read_text(encoding="utf-8")
    )
    assert "Azure for Startups" in capture["retained_context_blocks_note"]
    assert any(
        "No credit card required" in entry
        for entry in capture["duplicate_live_blocks_not_retained"]
    )


def test_pinning_the_students_card_claim_would_change_no_verdict() -> None:
    """The decision above is verdict-NEUTRAL, and that is measured, not claimed.

    ``student_program`` is in the classifier's temporary/conditional set, so even
    the most generous reading of the page -- no card AND no paid dependencies --
    reaches Z2 at best and never Z0. With paid dependencies unknown, which is the
    real state of the evidence, the verdict is UNKNOWN either way.
    """

    _, (candidate,) = run_extraction_case(
        "azure", "html", "azure-students", official_domains=DOMAINS
    )
    as_extracted = _classify(candidate.facts)
    assert as_extracted.zero_cost_class == "UNKNOWN"

    generous = dict(candidate.facts)
    generous["requires_card"] = False
    assert _classify(generous).zero_cost_class == "UNKNOWN"

    most_generous = classify(
        OfferFacts(
            offer_type="student_program",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    assert most_generous.zero_cost_class == "Z2_TEMPORARY_OR_CONDITIONAL"
    assert most_generous.zero_cost_class != "Z0_TRUE_FREE"


def test_the_four_azure_offer_kinds_are_never_conflated() -> None:
    """Perpetual, introductory, credit-backed and programme offers stay distinct."""

    extracted = {}
    for case in SOURCE_CASES:
        _, (candidate,) = run_extraction_case("azure", "html", case, official_domains=DOMAINS)
        extracted[str(candidate.facts["service"])] = dict(candidate.facts)

    # The credit-backed account.
    assert extracted["Azure free account"]["offer_type"] == "new_customer_credit"
    # The time-limited introductory window.
    assert extracted["Azure 12 months free services"]["offer_type"] == "trial"
    # The eligibility-gated programme.
    assert extracted["Azure for Students"]["offer_type"] == "student_program"
    # The monthly replenishing grant on a metered service.
    assert extracted["Azure DevOps Services"]["offer_type"] == "recurring_quota"
    # The ONE genuinely perpetual offer.
    assert extracted["Azure Cosmos DB"]["offer_type"] == "always_free"
    # The two whose commercial structure their own documents never establish.
    assert extracted["Azure App Service"]["offer_type"] == "other"
    assert extracted["Azure Static Web Apps"]["offer_type"] == "other"

    # A credit belongs to the account-level offer and to nothing else.
    assert extracted["Azure free account"]["credit_amount"] == "$200"
    assert "credit_amount" not in extracted["Azure Cosmos DB"]
    assert "credit_amount" not in extracted["Azure App Service"]
    # The perpetual offer carries no term, and the bounded one does.
    assert "trial_length_months" not in extracted["Azure Cosmos DB"]
    assert extracted["Azure 12 months free services"]["trial_length_months"] == "12"
    # Microsoft's own disambiguating sentence travels with the perpetual offer.
    assert "is different from the Azure free account" in str(extracted["Azure Cosmos DB"]["notes"])


def test_a_perpetual_azure_offer_is_still_a_billing_exposure() -> None:
    """The single most important finding in this slice.

    Cosmos DB's page is titled "Lifetime Free Tier" and says the tier "lasts
    indefinitely for the lifetime of the account" AND that usage above it is
    "billed at regular price". Perpetual does not mean Z0, and this proves the
    product says so using the real classifier rather than an author's summary.
    It is also an independent reproduction of the AWS Step Functions finding on
    a different provider and a different document.
    """

    _, (candidate,) = run_extraction_case(
        "azure", "html", "azure-cosmos-db-free-tier", official_domains=DOMAINS
    )
    assert candidate.facts["offer_type"] == "always_free"
    assert "lasts indefinitely for the lifetime of the account" in str(
        candidate.facts["availability"]
    )
    assert candidate.facts["exhaustion_behaviour"] == "automatic_billing"

    result = _classify(candidate.facts)
    assert result.zero_cost_class == "Z1_BILLING_EXPOSURE"
    assert any("automatic billing" in reason for reason in result.blocking_conditions)


def test_a_payment_method_is_required_and_that_is_published() -> None:
    """The unfavourable finding, asserted rather than quietly dropped."""

    _, (candidate,) = run_extraction_case(
        "azure", "html", "azure-free-account", official_domains=DOMAINS
    )
    assert candidate.facts["requires_card"] is True
    assert "credit card or a debit card" in str(candidate.facts["payment_method_basis"])
    # The "will initially NOT be charged" qualification travels with the card
    # fact instead of being dropped, and so does the authorization hold.
    assert "temporary authorization hold" in str(candidate.facts["payment_method_purpose"])
    assert candidate.facts["exhaustion_behaviour"] == "manual_upgrade_required"

    result = _classify(candidate.facts)
    assert result.zero_cost_class == "Z1_BILLING_EXPOSURE"
    assert any("payment card is required" in reason for reason in result.blocking_conditions)


def test_the_safest_azure_offer_is_two_unknowns_from_z0() -> None:
    """App Service is the closest Azure comes to Z0, and the reason is narrow.

    Its page states a genuinely SAFE, non-billing exhaustion behaviour, so it
    clears the billing gate entirely and every blocking condition it has is an
    *unknown* rather than an exposure.

    **It is TWO unknowns away, not one.** Gate 4 reports ``requires_card`` and
    ``has_paid_dependencies`` independently and this document states neither, so
    resolving the card alone still yields ``UNKNOWN``.

    This test previously read ``..._fails_only_on_the_card_gate`` and asserted
    only that every blocker was an unknown and that *some* blocker mentioned the
    card. Both held with TWO blockers present, so **it passed while its own name
    was false**. The fix is not the rename: it is asserting the EXACT blocking
    set, plus the discrimination control below which proves that assertion
    changes when the set changes. A test renamed accurately but asserting no more
    than before would be worse than the false name, because the name would then
    invite trust the assertions still could not support.
    """

    _, (candidate,) = run_extraction_case(
        "azure", "html", "azure-app-service-quotas", official_domains=DOMAINS
    )
    assert candidate.facts["exhaustion_behaviour"] == "site_disabled_until_reset"
    assert candidate.facts["exhaustion_behaviour"] in engine.SAFE_EXHAUSTION
    assert "requires_card" not in candidate.facts
    assert "has_paid_dependencies" not in candidate.facts

    result = _classify(candidate.facts)
    assert result.zero_cost_class == "UNKNOWN"

    # THE EXACT BLOCKING SET. Equality, not membership: an added, removed or
    # reworded blocker fails here rather than slipping past an `any(...)`.
    assert tuple(sorted(result.blocking_conditions)) == APP_SERVICE_BLOCKERS
    assert len(result.blocking_conditions) == 2
    # Neither blocker is a DEFINITE billing exposure. That is what "clears the
    # billing gate entirely" means, asserted against gate 3's own reason
    # vocabulary rather than by searching for the word "unknown".
    assert not set(result.blocking_conditions) & set(BILLING_GATE_REASONS)

    # DISCRIMINATION CONTROL, in the same test so the equality above is
    # non-vacuous BY CONSTRUCTION rather than by a reader's inspection. Resolving
    # the card leaves ONE blocker, so the exact-set assertion must no longer
    # hold -- and the offer is still not Z0, which is the substantive half of the
    # correction that failed evaluation.
    card_resolved = dict(candidate.facts) | {"requires_card": False}
    narrowed = _classify(card_resolved)
    assert tuple(sorted(narrowed.blocking_conditions)) != APP_SERVICE_BLOCKERS
    assert narrowed.blocking_conditions == (PAID_DEPS_UNKNOWN,)
    assert narrowed.zero_cost_class == "UNKNOWN"
    assert narrowed.zero_cost_class != "Z0_TRUE_FREE"


def test_exactly_one_of_nine_combinations_would_reach_z0() -> None:
    """The measurement behind "two unknowns", enumerated rather than asserted.

    Holding the document's own ``offer_type`` and ``exhaustion_behaviour``, the
    two tri-state billing facts admit nine combinations. Exactly one reaches Z0
    and it needs BOTH resolved favourably. This is what makes "two unknowns"
    a count rather than a turn of phrase.
    """

    _, (candidate,) = run_extraction_case(
        "azure", "html", "azure-app-service-quotas", official_domains=DOMAINS
    )
    reaching = []
    for card, deps in itertools.product([None, True, False], repeat=2):
        result = classify(
            OfferFacts(
                offer_type=str(candidate.facts["offer_type"]),
                requires_card=card,
                has_paid_dependencies=deps,
                exhaustion_behaviours=(str(candidate.facts["exhaustion_behaviour"]),),
            )
        )
        if result.zero_cost_class == "Z0_TRUE_FREE":
            reaching.append((card, deps))

    assert reaching == [(False, False)], f"expected exactly one Z0 route, saw {reaching}"
    assert len(reaching) == 1


def test_offer_type_other_is_not_a_safety_mechanism() -> None:
    """`other` is Z0-CAPABLE. Rule 3 protects nothing on its own.

    ``docs/DATA_MODEL.md`` rule 3 says to use ``other`` and "route the candidate
    for review until the structure is evidenced". That is an instruction to the
    AUTHOR; the classifier implements no such routing. Both rule-3 offers in this
    slice (App Service, Static Web Apps) are withheld from Z0 by their unknown
    billing facts and by the publication gate -- **never by their offer type**.

    Pinned so the assumption is a tested property rather than something a reader
    takes on trust, because it is what keeps this slice's near-miss safe.

    DRIFT DETECTOR, read the failure message before "fixing" this. If a later
    slice adds ``other`` to ``TEMPORARY_CONDITIONAL_OFFER_TYPES`` this test goes
    RED **because the engine became SAFER**. That is not a regression: update
    this test deliberately to record the new behaviour. Do NOT delete it and do
    NOT weaken it to green.
    """

    safer = "other" in engine.TEMPORARY_CONDITIONAL_OFFER_TYPES
    assert not safer, (
        "'other' is now in TEMPORARY_CONDITIONAL_OFFER_TYPES. The engine has become "
        "SAFER than when this test was written -- this is an IMPROVEMENT, not a "
        "regression. Update this test deliberately to record that rule-3 offers are "
        "now gated by their offer type, and update the docstrings in "
        "app/ingest/adapters/profiles/azure.py and docs/PROVIDER_ADAPTERS.md that "
        "state the opposite. Do not delete this test and do not weaken it to green."
    )
    assert "other" not in engine.SELF_HOSTED_OFFER_TYPES

    # The positive measurement: `other` reaches Z0 when every billing gate is
    # explicitly clear, so it cannot be what withholds Z0 anywhere.
    probe = classify(
        OfferFacts(
            offer_type="other",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("site_disabled_until_reset",),
        )
    )
    assert probe.zero_cost_class == "Z0_TRUE_FREE"
    assert not probe.blocking_conditions

    # And the two rule-3 offers really do carry that type, so this is not an
    # abstract statement about a value nothing uses.
    rule_three = {}
    for case in ("azure-app-service-quotas", "azure-static-web-apps-plans"):
        _, (candidate,) = run_extraction_case("azure", "html", case, official_domains=DOMAINS)
        rule_three[case] = dict(candidate.facts)
        assert candidate.facts["offer_type"] == "other"
        # Each is still withheld, and no blocker is a DEFINITE billing
        # exposure -- which is what "clears the billing gate" means. Asserted
        # against gate 3's own reason vocabulary rather than by looking for
        # the word "unknown", because gate 4's "No quota data is available to
        # confirm a safe exhaustion behaviour." does not contain it.
        result = _classify(candidate.facts)
        assert result.zero_cost_class == "UNKNOWN"
        assert not set(result.blocking_conditions) & set(BILLING_GATE_REASONS)
        assert not any("offer type" in reason.lower() for reason in result.blocking_conditions)

    # Static Web Apps carries a THIRD blocker (no quota data at all), so the two
    # rule-3 offers are not equally close to Z0 and this test does not imply they
    # are.
    swa = _classify(rule_three["azure-static-web-apps-plans"])
    assert len(swa.blocking_conditions) == 3


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_no_azure_offer_can_reach_z0(case: str) -> None:
    _, (candidate,) = run_extraction_case("azure", "html", case, official_domains=DOMAINS)
    result = _classify(candidate.facts)
    assert result.zero_cost_class != "Z0_TRUE_FREE"
    assert result.blocking_conditions


def test_the_z0_sweep_is_not_vacuous() -> None:
    """POSITIVE CONTROL for the sweep above.

    Without this, "no Azure offer reached Z0" would be indistinguishable from
    "the classifier cannot emit Z0 at all", and the zero count would prove
    nothing.
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


def test_the_positive_control_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A control that cannot fail proves nothing, so this makes it fail.

    The engine is BROKEN in the one way that would silently invalidate the sweep
    above: its safe-exhaustion partition is emptied, so no exhaustion behaviour
    can ever be confirmed safe and Z0 becomes unreachable. The control must go
    RED and the Azure sweep must stay GREEN. That asymmetry is the whole point --
    it shows the sweep's zero count is carried by the control rather than by the
    classifier being incapable of the verdict.
    """

    # Baseline: on the real engine the control passes.
    healthy = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    assert healthy.zero_cost_class == "Z0_TRUE_FREE"

    print(
        "PATCHED LINE: app.classify.engine.SAFE_EXHAUSTION = frozenset()  "
        f"(was {len(engine.SAFE_EXHAUSTION)} safe behaviours)"
    )
    monkeypatch.setattr(engine, "SAFE_EXHAUSTION", frozenset())

    broken = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    # THE CONTROL FAILS. Under the patched engine the assertion made by
    # test_the_z0_sweep_is_not_vacuous no longer holds.
    assert broken.zero_cost_class != "Z0_TRUE_FREE"
    assert broken.zero_cost_class == "UNKNOWN"

    # THE SWEEP SURVIVES. Every Azure offer is still non-Z0, so the sweep alone
    # would have reported success against a classifier that cannot emit Z0.
    for case in SOURCE_CASES:
        _, (candidate,) = run_extraction_case("azure", "html", case, official_domains=DOMAINS)
        assert _classify(candidate.facts).zero_cost_class != "Z0_TRUE_FREE"


@pytest.mark.parametrize(
    ("offer_type", "requires_card", "has_paid_dependencies", "expected"),
    [
        # A programme offer cannot reach Z0 on ANY path, including the
        # hypothetical where every billing gate is explicitly clear. That proves
        # the non-Z0 outcome comes from the offer TYPE, not merely from the card.
        ("student_program", True, False, "Z1_BILLING_EXPOSURE"),
        ("student_program", None, None, "UNKNOWN"),
        ("student_program", False, False, "Z2_TEMPORARY_OR_CONDITIONAL"),
        # The time-limited introductory window behaves identically.
        ("trial", True, False, "Z1_BILLING_EXPOSURE"),
        ("trial", None, None, "UNKNOWN"),
        ("trial", False, False, "Z2_TEMPORARY_OR_CONDITIONAL"),
        # And so does the credit grant.
        ("new_customer_credit", True, False, "Z1_BILLING_EXPOSURE"),
        ("new_customer_credit", None, None, "UNKNOWN"),
        ("new_customer_credit", False, False, "Z2_TEMPORARY_OR_CONDITIONAL"),
    ],
)
def test_programme_and_time_limited_offers_are_unreachable_from_z0(
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
        "Azure free account",
        "Azure 12 months free services",
        "Azure for Students",
        "Azure Cosmos DB",
        "Azure App Service",
        "Azure Static Web Apps",
        "Azure DevOps Services",
    }
    # Nothing may claim a verified free tier: every extracted Azure offer
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
        _, (candidate,) = run_extraction_case("azure", "html", case, official_domains=DOMAINS)
        extracted.add(str(candidate.facts["service"]))
    assert set(config.service_categories) == extracted


def test_both_official_hosts_are_declared_and_no_source_leaves_them() -> None:
    config = load_and_validate(CONFIG_PATH)
    assert config.provider.official_domains == ["azure.microsoft.com", "learn.microsoft.com"]
    for source in config.sources:
        host = (source.url or "").split("://", 1)[-1].split("/", 1)[0]
        assert host in set(config.provider.official_domains)


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


STOP_ANCHOR = "the app is stopped until the quota resets"
ROW_ANCHOR = "Filesystem"


def _mutate(source: str, mutation: str) -> str:
    """Apply exactly one named mutation, refusing an ambiguous anchor."""

    def once(old: str, new: str) -> str:
        assert source.count(old) == 1, f"{mutation}: anchor matched {source.count(old)} times"
        print(f"PATCHED LINE [{mutation}]: {old.strip()[:110]!r}  ->  {new.strip()[:110]!r}")
        return source.replace(old, new)

    pinned = _block_containing(source, "p", STOP_ANCHOR)
    last_row = _block_containing(source, "tr", ROW_ANCHOR)

    if mutation == "assertion_deleted":
        return once(pinned, "")
    if mutation == "assertion_reworded":
        return once(pinned, pinned.replace("an HTTP 403 error", "an HTTP 429 error"))
    if mutation == "assertion_truncated":
        return once(pinned, "<p>If an app exceeds the CPU (Short) quota, the app is stopped.</p>")
    if mutation == "assertion_duplicated":
        return once(pinned, pinned + "\n  " + pinned)
    if mutation == "unrelated_paragraph_added":
        # DISCRIMINATION CONTROL. An edit that touches no pinned block and no
        # mapped row must leave the verdict intact. A guard that rejects this is
        # rejecting change rather than detecting drift.
        return once(pinned, pinned + "\n  <p>Azure regions are listed on a separate page.</p>")
    if mutation == "undeclared_matrix_row":
        return once(
            last_row,
            last_row
            + '\n      <tr><th scope="row">Some Future Quota</th>'
            + "<td>A quota Microsoft has not published yet.</td></tr>",
        )
    if mutation == "mapped_row_removed":
        return once(last_row, "")
    if mutation == "renamed_tier":
        return once('<th scope="col">Description</th>', '<th scope="col">Descriptions</th>')
    if mutation == "duplicated_table":
        table = source.split("<table>", 1)[1].split("</table>", 1)[0]
        return once("</table>", f"</table>\n  <table>{table}</table>")
    if mutation == "extra_column":
        # Header AND body rows gain a cell, so every row keeps a consistent width
        # and the declared signature is still a subset of the live headers.
        head, body = source.split("<tbody>", 1)
        head = head.replace(
            '<th scope="col">Description</th>',
            '<th scope="col">Description</th><th scope="col">Notes</th>',
        )
        return head + "<tbody>" + body.replace("</tr>", "<td>x</td></tr>")
    if mutation == "whitespace_entities":
        return once(
            '<th scope="row">Filesystem</th>',
            '<th scope="row">  File&#115;ystem  </th>',
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
        # DISCRIMINATION AND FALSE-POSITIVE CONTROLS. A guard that rejects these
        # would be loosened by the next author, which is worse than no guard.
        ("unrelated_paragraph_added", None),
        ("extra_column", None),
        ("whitespace_entities", None),
    ],
)
def test_predicted_mutations_match_observation(mutation: str, expected_error: str | None) -> None:
    fixture = load_case("azure", "html", "azure-app-service-quotas")
    source = fixture.source_path.read_text(encoding="utf-8")
    adapter = build_fixture_adapter(
        fixture, official_domains=DOMAINS, body=_mutate(source, mutation).encode()
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts.get("error") == expected_error


def test_the_unrelated_edit_leaves_every_published_fact_identical() -> None:
    """The discrimination control, checked on VALUES and not only on absence.

    "No error" is a weak claim: a guard could survive the edit while quietly
    dropping a fact. This asserts the whole extracted fact set is byte-identical
    to the unmutated one.
    """

    fixture = load_case("azure", "html", "azure-app-service-quotas")
    source = fixture.source_path.read_text(encoding="utf-8")

    baseline = build_fixture_adapter(fixture, official_domains=DOMAINS)
    (before,) = baseline.extract(baseline.canonicalize(baseline.fetch(fixture.source_url)))

    mutated = build_fixture_adapter(
        fixture,
        official_domains=DOMAINS,
        body=_mutate(source, "unrelated_paragraph_added").encode(),
    )
    (after,) = mutated.extract(mutated.canonicalize(mutated.fetch(fixture.source_url)))

    assert dict(after.facts) == dict(before.facts)
    assert after.verification_state == "candidate"
    assert len(after.evidence) == len(before.evidence)


def _run_with_profile(case: str, profile: HtmlExtractionProfile, body: bytes):
    fixture = load_case("azure", "html", case)
    adapter = HtmlDocAdapter(
        FixtureFetcher(
            {fixture.source_url: (body, "text/html")}, FetchPolicy(official_domains=DOMAINS)
        ),
        source_urls=(fixture.source_url,),
        profile=profile,
        provider="azure",
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

    fixture = load_case("azure", "html", "azure-app-service-quotas")
    source = fixture.source_path.read_text(encoding="utf-8")
    deleted = _mutate(source, "assertion_deleted").encode()
    profile = resolve_profile("azure_app_service_quotas")

    # Baseline: with the real profile the guard fires on this input.
    baseline = _run_with_profile("azure-app-service-quotas", profile, deleted)
    assert baseline.facts.get("error") == "assertion_not_found"

    # PATCH: every assertion on the deleted block becomes optional. This is the
    # single line of behaviour under test, printed so a reader can see exactly
    # what was disabled.
    patched_assertions = tuple(
        dataclasses.replace(a, required=False) if STOP_ANCHOR in a.text else a
        for a in profile.assertions
    )
    patched = dataclasses.replace(profile, assertions=patched_assertions)
    disabled = [a.field for a in patched.assertions if not a.required]
    print(f"PATCHED LINE: HtmlTextAssertion(required=True -> False) for fields {disabled}")
    assert disabled, "the patch must actually disable something"

    # The target error is now UNPRODUCIBLE on an input that should raise it.
    weakened = _run_with_profile("azure-app-service-quotas", patched, deleted)
    assert weakened.facts.get("error") != "assertion_not_found"
    assert weakened.verification_state == "candidate"
    # And the damage is exactly what the guard existed to prevent: a free plan
    # published with NO exhaustion behaviour at all.
    assert "exhaustion_behaviour" not in weakened.facts

    # COLLATERAL CONTROL: a healthy document must still extract under the patched
    # profile. Without this we could not tell "the guard stopped firing" from
    # "something upstream collapsed and nothing extracts at all".
    healthy = _run_with_profile("azure-app-service-quotas", patched, fixture.content)
    assert healthy.verification_state == "candidate"
    assert healthy.facts["service"] == "Azure App Service"
    assert healthy.facts["exhaustion_behaviour"] == "site_disabled_until_reset"


def test_the_row_completeness_guard_is_what_produces_unknown_matrix_rows() -> None:
    """Mutation test for the matrix guard, same shape as the assertion one."""

    fixture = load_case("azure", "html", "azure-app-service-quotas")
    source = fixture.source_path.read_text(encoding="utf-8")
    injected = _mutate(source, "undeclared_matrix_row").encode()
    profile = resolve_profile("azure_app_service_quotas")

    baseline = _run_with_profile("azure-app-service-quotas", profile, injected)
    assert baseline.facts.get("error") == "unknown_matrix_rows"
    assert "Some Future Quota" in baseline.facts["detail"]

    # PATCH: declare the injected row, so completeness can no longer be violated.
    patched_rows = dict(profile.matrix_rows)
    patched_rows["some future quota"] = next(iter(profile.matrix_rows.values()))
    patched = dataclasses.replace(profile, matrix_rows=patched_rows)
    print(
        "PATCHED LINE: matrix_rows += {'some future quota': HtmlMatrixRow(...)} "
        f"({len(profile.matrix_rows)} -> {len(patched.matrix_rows)} declared rows)"
    )

    weakened = _run_with_profile("azure-app-service-quotas", patched, injected)
    assert weakened.facts.get("error") != "unknown_matrix_rows"

    # COLLATERAL CONTROL. The patched profile declares a row the healthy document
    # does not contain, so a healthy document must now fail for a DIFFERENT and
    # named reason rather than passing silently or failing for the old one.
    healthy = _run_with_profile("azure-app-service-quotas", patched, fixture.content)
    assert healthy.facts.get("error") == "missing_matrix_rows"


def test_deleting_the_overage_sentence_rejects_rather_than_publishing_a_free_claim() -> None:
    """The sentence that makes the PERPETUAL offer non-free is load-bearing.

    Cosmos DB is the offer most able to produce a false $0 claim in this
    provider, because Microsoft itself calls the tier lifetime. Without the
    billing sentence the document must be REJECTED rather than degrade to a
    perpetual offer whose exhaustion is merely unknown -- which a downstream
    reader could mistake for a safe stop.
    """

    fixture = load_case("azure", "html", "azure-cosmos-db-free-tier")
    source = fixture.source_path.read_text(encoding="utf-8")
    anchor = _block_containing(source, "p", "billed at regular price")
    mutated = source.replace(anchor, "")
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "assertion_not_found"
    assert "offer_type" not in candidate.facts


def test_deleting_the_payment_method_sentence_rejects_the_free_account_document() -> None:
    """Removing the card evidence must not silently produce a cardless offer."""

    fixture = load_case("azure", "html", "azure-free-account")
    source = fixture.source_path.read_text(encoding="utf-8")
    anchor = _block_containing(source, "p", "a credit card or a debit card (non-prepaid)")
    mutated = source.replace(anchor, "")
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "assertion_not_found"
    assert "requires_card" not in candidate.facts


def test_deleting_the_perpetuity_sentence_rejects_rather_than_downgrading_silently() -> None:
    """The perpetuity claim is evidence, not a default.

    Without the "lasts indefinitely" block the document must be rejected rather
    than quietly producing an offer with some other type: an `always_free`
    verdict that outlived its sentence is exactly the unsupported claim this
    product forbids.
    """

    fixture = load_case("azure", "html", "azure-cosmos-db-free-tier")
    source = fixture.source_path.read_text(encoding="utf-8")
    anchor = _block_containing(source, "p", "Free tier lasts indefinitely")
    mutated = source.replace(anchor, "")
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "assertion_not_found"
    assert candidate.facts.get("offer_type") is None


def test_the_healthy_documents_still_extract_after_the_mutation_battery() -> None:
    """Collateral control: the mutations above proved nothing if these now fail."""

    _, (app_service,) = run_extraction_case(
        "azure", "html", "azure-app-service-quotas", official_domains=DOMAINS
    )
    assert app_service.verification_state == "candidate"
    assert app_service.facts["cpu_day_quota"].startswith("The total amount of CPU allowed")
    assert len(app_service.evidence) == 14

    _, (cosmos,) = run_extraction_case(
        "azure", "html", "azure-cosmos-db-free-tier", official_domains=DOMAINS
    )
    assert cosmos.verification_state == "candidate"
    assert cosmos.facts["free_storage"] == "25 GB"
    assert cosmos.facts["free_request_units_per_second"] == "1000"
    assert len(cosmos.evidence) == 10

    _, (static_web_apps,) = run_extraction_case(
        "azure", "html", "azure-static-web-apps-plans", official_domains=DOMAINS
    )
    assert static_web_apps.verification_state == "candidate"
    assert static_web_apps.facts["staging_environments"] == "3 per app"
    assert len(static_web_apps.evidence) == 17


def test_a_pinned_block_published_twice_yields_ambiguous_assertion() -> None:
    """Proof that whole-block equality REQUIRES exactly one match.

    Microsoft publishes several strings more than once per page (the Students
    page publishes "No credit card required" as both a heading and a bullet).
    The generator refuses to pin any block that occurs more than once in its own
    scope, so no committed capture reproduces such a case; this shows what
    pinning one would actually do.
    """

    fixture = load_case("azure", "html", "azure-app-service-quotas")
    source = fixture.source_path.read_text(encoding="utf-8")
    duplicated = _mutate(source, "assertion_duplicated").encode()

    # `value` is a VERBATIM clause of the block this pins, not a sentinel. The
    # free-text verbatim rule is enforced at construction now (F008 S4 prereq),
    # so a probe that pinned `notes` to a placeholder would be refused before it
    # could demonstrate anything about ambiguity.
    greedy = HtmlExtractionProfile(
        name="azure_duplicate_probe",
        mode="assertions",
        trusted_assertions=True,
        assertions=(
            HtmlTextAssertion(
                text=(
                    "If an app exceeds the CPU (Short), CPU (Day), or Bandwidth quota, the app "
                    "is stopped until the quota resets. During this time, all incoming requests "
                    "result in an HTTP 403 error."
                ),
                field="notes",
                value="all incoming requests result in an HTTP 403 error.",
            ),
        ),
        required_fields=("notes",),
    )
    adapter = HtmlDocAdapter(
        FixtureFetcher(
            {fixture.source_url: (duplicated, "text/html")},
            FetchPolicy(official_domains=DOMAINS),
        ),
        source_urls=(fixture.source_url,),
        profile=greedy,
        provider="azure",
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "ambiguous_assertion"


def test_captures_declare_synthetic_provenance_where_it_applies() -> None:
    for case in DOCUMENT_CASES:
        capture = json.loads((FIXTURE_ROOT / case / "capture.json").read_text(encoding="utf-8"))
        assert capture["synthetic"] is True
        assert capture["sha256_original"] is None
        assert "NEGATIVE TEST FIXTURE" in capture["negative_fixture_note"]
        assert capture["mutation"]
        assert capture["mutation_intent"]
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
        assert "REFUSES to write" in capture["trim_method"]


def test_the_payment_method_block_is_the_one_the_capture_pinned() -> None:
    """Belt and braces: the Z1 verdict traces to a hash in the committed sidecar."""

    capture = json.loads(
        (FIXTURE_ROOT / "azure-free-account" / "capture.json").read_text(encoding="utf-8")
    )
    digest = hashlib.sha256(PAYMENT_METHOD_REQUIRED.encode("utf-8")).hexdigest()
    assert digest in capture["structure"]["asserted_block_sha256"]
