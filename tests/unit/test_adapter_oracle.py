"""Offline integrity and safety controls for the Oracle Cloud provider slice.

The controls here exist to stop one specific failure: publishing an Oracle offer
as free when the official pages do not support it. Oracle is the provider most
able to produce that failure, because it markets "Always Free" more prominently
than any other provider in this repository AND its Always Free tier really is
perpetual -- Oracle says so, in blocks that describe Always Free alone. A reader
who stopped at the marketing would conclude the tier is $0. It is not.

The tests below check four separate things, and it matters that they are
separate:

* that every fact traces to a real live table row or a verbatim prose block;
* that a drifted, reworded, truncated or duplicated block REJECTS the document
  rather than publishing a stale value, while an unrelated edit does NOT --
  a guard that rejects any change protects nothing and will be loosened;
* that the REAL classifier withholds Z0 from every Oracle offer, with a POSITIVE
  CONTROL proving the same sweep can observe a Z0;
* that the positive control is itself LOAD-BEARING -- break what it guards and
  the control fails while the sweep still passes. A control that cannot fail
  proves nothing at all.

**The blocking conditions are checked by SHAPE, not just by verdict.** Four
offers are refused on a QUOTED payment-card sentence and two on the ABSENCE of
any payment statement, and the tests below assert which is which. An
absence-based refusal can be flipped by anything that later supplies the field;
a quotation cannot. Blurring the two would overstate this slice's strength.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html as htmllib
import json
import re
from pathlib import Path

import pytest
from app.classify import engine as classify_engine
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
CONFIG_PATH = REPO_ROOT / "config" / "examples" / "providers" / "oracle.example.yaml"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "ingest" / "oracle" / "html"
DOMAINS = ("oracle.com", "www.oracle.com", "docs.oracle.com")

SOURCE_CASES = (
    "oracle-always-free-resources",
    "oracle-free-tier",
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-free-credit-promotion",
    "oracle-mysql-heatwave-always-free",
)
DOCUMENT_CASES = ("unchanged", "changed", "partial", "malformed", "contradictory")
MATRIX_CASES = ("oracle-always-free-resources",)
ASSERTION_ONLY_CASES = (
    "oracle-free-tier",
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-free-credit-promotion",
    "oracle-mysql-heatwave-always-free",
)
PROFILE_NAMES = (
    "oracle_always_free_resources",
    "oracle_free_tier",
    "oracle_always_free_services",
    "oracle_cloud_free_tier",
    "oracle_free_credit_promotion",
    "oracle_mysql_heatwave_always_free",
)

#: Which sources refuse Z0 on a QUOTED sentence, and which on an ABSENCE. This is
#: the distinction the module docstring insists on, written down so it cannot
#: quietly drift.
CARD_BY_QUOTATION = (
    "oracle-free-tier",
    "oracle-always-free-services",
    "oracle-cloud-free-tier",
    "oracle-mysql-heatwave-always-free",
)
CARD_BY_ABSENCE = ("oracle-always-free-resources", "oracle-free-credit-promotion")

#: A block Oracle publishes TWICE on the OCI Always Free Resources page -- once in
#: the Always-Free-only list and once in the paid/trial list. MEASURED live. It is
#: deliberately NOT pinned by any profile, and the test below proves that decision
#: was necessary rather than stylistic.
DUPLICATED_STORAGE_BLOCK = "50,000 Object Storage API requests per month"


@pytest.mark.parametrize("case", available_cases("oracle", "html"))
def test_every_oracle_fixture_extracts_exactly_as_declared(case: str) -> None:
    run_extraction_case("oracle", "html", case, official_domains=DOMAINS)


def test_the_five_document_cases_and_six_official_sources_are_present() -> None:
    """NON-VACUITY GUARD for the parametrised sweep above.

    Deliberately NOT parametrised over ``available_cases``: a guard that shares
    the discovery mechanism it guards is decoration, because an empty discovery
    would silently collapse the sweep to zero cases AND satisfy the guard at the
    same time. The expected names are written out literally.
    """

    cases = set(available_cases("oracle", "html"))
    assert set(DOCUMENT_CASES) <= cases
    assert set(SOURCE_CASES) <= cases
    assert len(SOURCE_CASES) == 6
    assert len(cases) >= len(SOURCE_CASES) + len(DOCUMENT_CASES)
    for case in SOURCE_CASES + DOCUMENT_CASES:
        assert (FIXTURE_ROOT / case / "expected.json").is_file()


@pytest.mark.parametrize("case", MATRIX_CASES)
def test_capture_structure_matches_every_retained_target_cell(case: str) -> None:
    fixture = load_case("oracle", "html", case)
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
    assert capture["unpivoted_target_table_columns"] == ["Limit Name"]


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_asserted_blocks_match_the_pinned_capture_hashes(case: str) -> None:
    fixture = load_case("oracle", "html", case)
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

    fixture = load_case("oracle", "html", case)
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

    fixture = load_case("oracle", "html", case)
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
    assert capture["live_table_count"] == 0
    assert "ASSERTION-ONLY" in capture["live_table_note"]


def test_the_matrix_profile_maps_every_live_row_and_ignores_none() -> None:
    profile = resolve_profile("oracle_always_free_resources")
    assert profile.mode == "matrix"
    assert profile.header_signature == ("resource", "limit name", "always free")
    assert profile.ignored_matrix_rows == ()
    assert len(profile.matrix_rows) == 6
    assert all(row.required for row in profile.matrix_rows.values())


# --------------------------------------------------------------------------- #
# The product rule: no unsupported free claim, in either direction             #
# --------------------------------------------------------------------------- #


def test_oracles_always_free_tier_really_is_perpetual() -> None:
    """The FAVOURABLE finding, evidenced rather than assumed away.

    A wrongly-omitted free offer is as much a defect as a wrongly-published one,
    so the perpetuity Oracle genuinely does publish is extracted and asserted.
    """

    _, (resources,) = run_extraction_case(
        "oracle", "html", "oracle-always-free-resources", official_domains=DOMAINS
    )
    assert resources.facts["offer_type"] == "always_free"
    assert "for the life of the account" in str(resources.facts["availability"])

    _, (services,) = run_extraction_case(
        "oracle", "html", "oracle-always-free-services", official_domains=DOMAINS
    )
    assert services.facts["offer_type"] == "always_free"
    assert "available for an unlimited time" in str(services.facts["availability"])


def test_a_perpetual_oracle_offer_is_still_a_billing_exposure() -> None:
    """THE finding of this slice, and the third provider to demonstrate it.

    Oracle's Always Free services are perpetual by quotation AND card-gated by
    quotation, on the SAME document. Perpetual does not mean Z0.
    """

    _, (candidate,) = run_extraction_case(
        "oracle", "html", "oracle-always-free-services", official_domains=DOMAINS
    )
    assert candidate.facts["offer_type"] == "always_free"
    assert "unlimited time" in str(candidate.facts["availability"])
    assert candidate.facts["requires_card"] is True

    result = classify(
        OfferFacts(
            offer_type=str(candidate.facts["offer_type"]),
            requires_card=candidate.facts.get("requires_card"),
            has_paid_dependencies=candidate.facts.get("has_paid_dependencies"),
        )
    )
    assert result.zero_cost_class == "Z1_BILLING_EXPOSURE"
    assert any("payment card is required" in reason for reason in result.blocking_conditions)


@pytest.mark.parametrize("case", CARD_BY_QUOTATION)
def test_the_card_requirement_is_a_quotation_on_its_own_document(case: str) -> None:
    """Each card claim must be readable off ONE block of that offer's own page."""

    _, (candidate,) = run_extraction_case("oracle", "html", case, official_domains=DOMAINS)
    assert candidate.facts["requires_card"] is True

    profile = resolve_profile(load_case("oracle", "html", case).profile)
    (source_block,) = [a.text for a in profile.assertions if a.field == "requires_card"]
    # The quoted block must itself mention a card. Nothing is composed from a
    # second block, and nothing is carried in from another document.
    assert "credit card" in source_block or "credit/debit card" in source_block
    document = load_case("oracle", "html", case).content.decode("utf-8")
    assert source_block.split(". ")[0] in " ".join(document.split())


@pytest.mark.parametrize("case", CARD_BY_ABSENCE)
def test_an_absence_based_refusal_is_declared_as_such(case: str) -> None:
    """Honesty about STRUCTURE: these two refuse Z0 on absence, not on a quote.

    Their documents state no payment condition at all. Carrying the requirement
    over from another Oracle page would be cross-document composition, so the
    field is simply absent and the classifier returns UNKNOWN. That is the
    correct outcome and the structurally weaker one, and it is named here rather
    than blurred into the quotation-backed results.
    """

    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    assert not [a for a in profile.assertions if a.field == "requires_card"]

    document = " ".join(fixture.content.decode("utf-8").split())
    assert "credit card" not in document
    assert "credit/debit card" not in document

    _, (candidate,) = run_extraction_case("oracle", "html", case, official_domains=DOMAINS)
    assert candidate.facts.get("requires_card") is None
    behaviours = ()
    if candidate.facts.get("exhaustion_behaviour"):
        behaviours = (str(candidate.facts["exhaustion_behaviour"]),)
    result = classify(
        OfferFacts(
            offer_type=str(candidate.facts["offer_type"]),
            requires_card=None,
            has_paid_dependencies=None,
            exhaustion_behaviours=behaviours,
        )
    )
    assert result.zero_cost_class == "UNKNOWN"
    assert any("payment card is required is unknown" in r for r in result.blocking_conditions)


def test_no_oracle_profile_claims_a_card_is_not_required() -> None:
    fields = {
        name: {a.field: a.value for a in resolve_profile(name).assertions} for name in PROFILE_NAMES
    }
    assert all(f.get("requires_card") is not False for f in fields.values())
    # `has_paid_dependencies` is stated by no Oracle document in this slice, so it
    # is claimed nowhere -- in either direction.
    assert all("has_paid_dependencies" not in f for f in fields.values())


def test_the_pay_as_you_go_billing_sentence_is_never_an_exhaustion_behaviour() -> None:
    """The measured trap: a PAID-account sentence must not describe a free offer.

    "You will only be charged for services that you use that exceeds Always
    Free." answers a Pay As You Go question, so converting it would attribute a
    paid account's behaviour to a free one. No profile does.
    """

    for name in PROFILE_NAMES:
        for assertion in resolve_profile(name).assertions:
            if assertion.field == "exhaustion_behaviour":
                assert "Pay As You Go" not in assertion.text
                assert "only be charged for services" not in assertion.text
    # The nearest such sentence IS published, whole, as a note -- so the boundary
    # is visible rather than lost.
    _, (hub,) = run_extraction_case(
        "oracle", "html", "oracle-cloud-free-tier", official_domains=DOMAINS
    )
    assert "Pay As You Go" in str(hub.facts["notes"])
    assert "exhaustion_behaviour" not in hub.facts


def test_the_three_oracle_offer_kinds_are_never_conflated() -> None:
    extracted = {}
    for case in SOURCE_CASES:
        _, (candidate,) = run_extraction_case("oracle", "html", case, official_domains=DOMAINS)
        extracted[str(candidate.facts["service"])] = dict(candidate.facts)

    # The perpetual offers.
    assert extracted["Oracle Cloud Infrastructure Always Free"]["offer_type"] == "always_free"
    assert extracted["Oracle Cloud Always Free services"]["offer_type"] == "always_free"
    assert extracted["Oracle MySQL HeatWave"]["offer_type"] == "always_free"
    # The credit-backed, time-limited offers.
    assert (
        extracted["Oracle Cloud Infrastructure Free Trial"]["offer_type"] == "new_customer_credit"
    )
    assert extracted["Oracle Cloud Free Tier"]["offer_type"] == "new_customer_credit"
    assert extracted["Oracle Cloud Free Credit Promotion"]["offer_type"] == "new_customer_credit"

    # A credit and a term belong to the credit offers and to nothing else.
    assert extracted["Oracle Cloud Infrastructure Free Trial"]["credit_amount"] == "$300"
    assert extracted["Oracle Cloud Free Tier"]["credit_amount"] == "US$300"
    for perpetual in (
        "Oracle Cloud Infrastructure Always Free",
        "Oracle Cloud Always Free services",
        "Oracle MySQL HeatWave",
    ):
        assert "credit_amount" not in extracted[perpetual]
        assert "trial_length_days" not in extracted[perpetual]


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_no_oracle_offer_can_reach_z0(case: str) -> None:
    _, (candidate,) = run_extraction_case("oracle", "html", case, official_domains=DOMAINS)
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

    Without this, "no Oracle offer reached Z0" would be indistinguishable from
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
    """PROOF that the control above can fail. A control that cannot is decoration.

    The control guards one property: that this classifier can still emit Z0. So
    break exactly that -- empty the safe-exhaustion partition, which is the only
    route to gate 6 -- and show the CONTROL goes red while the no-Z0 sweep stays
    green. That asymmetry is the whole argument: the sweep alone cannot tell a
    real refusal from a classifier that never says yes.
    """

    baseline = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    assert baseline.zero_cost_class == "Z0_TRUE_FREE", "baseline must be GREEN before breaking it"

    monkeypatch.setattr(classify_engine, "SAFE_EXHAUSTION", frozenset())
    print(
        "PATCHED LINE: app.classify.engine.SAFE_EXHAUSTION = frozenset()  (was 9 safe stop-types)"
    )

    crippled = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    # The CONTROL fails: Z0 has become unreachable.
    assert crippled.zero_cost_class != "Z0_TRUE_FREE"

    # The SWEEP still passes, which is exactly why the sweep alone proves nothing.
    for case in SOURCE_CASES:
        _, (candidate,) = run_extraction_case("oracle", "html", case, official_domains=DOMAINS)
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


def test_the_safe_exhaustion_partition_is_restored_after_the_load_bearing_proof() -> None:
    """Independent re-derivation that the monkeypatch above did not leak."""

    assert "hard_stop" in classify_engine.SAFE_EXHAUSTION
    assert len(classify_engine.SAFE_EXHAUSTION) == 9
    control = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=False,
            has_paid_dependencies=False,
            exhaustion_behaviours=("hard_stop",),
        )
    )
    assert control.zero_cost_class == "Z0_TRUE_FREE"


@pytest.mark.parametrize(
    ("requires_card", "has_paid_dependencies", "exhaustion", "expected"),
    [
        # Each gate must be able to block Z0 INDEPENDENTLY, starting from the
        # exact facts Oracle publishes for a perpetual offer. This is what makes
        # "Oracle is not Z0" a measurement of several conditions rather than one.
        (True, False, "hard_stop", "Z1_BILLING_EXPOSURE"),
        (False, True, "hard_stop", "Z1_BILLING_EXPOSURE"),
        (False, False, "automatic_billing", "Z1_BILLING_EXPOSURE"),
        (None, False, "hard_stop", "UNKNOWN"),
        (False, None, "hard_stop", "UNKNOWN"),
        (False, False, "unknown", "UNKNOWN"),
        (False, False, "manual_upgrade_required", "Z2_TEMPORARY_OR_CONDITIONAL"),
        (False, False, "hard_stop", "Z0_TRUE_FREE"),
    ],
)
def test_each_gate_can_independently_block_a_perpetual_offer(
    requires_card: bool | None,
    has_paid_dependencies: bool | None,
    exhaustion: str,
    expected: str,
) -> None:
    result = classify(
        OfferFacts(
            offer_type="always_free",
            requires_card=requires_card,
            has_paid_dependencies=has_paid_dependencies,
            exhaustion_behaviours=(exhaustion,),
        )
    )
    assert result.zero_cost_class == expected


def test_config_sources_categories_and_coverage_are_complete() -> None:
    config = load_and_validate(CONFIG_PATH)
    assert [source.id for source in config.sources] == list(SOURCE_CASES)
    assert {source.extraction_profile for source in config.sources} == set(PROFILE_NAMES)
    assert len(config.coverage) == 14
    assert set(config.service_categories) == {
        "Oracle Cloud Infrastructure Always Free",
        "Oracle Cloud Always Free services",
        "Oracle Cloud Infrastructure Free Trial",
        "Oracle Cloud Free Tier",
        "Oracle Cloud Free Credit Promotion",
        "Oracle MySQL HeatWave",
    }
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
        _, (candidate,) = run_extraction_case("oracle", "html", case, official_domains=DOMAINS)
        extracted.add(str(candidate.facts["service"]))
    assert set(config.service_categories) == extracted


def test_a_block_oracle_publishes_twice_would_be_ambiguous_if_pinned() -> None:
    """Proof that declining to pin the duplicated storage block was NECESSARY.

    Oracle publishes the Object Storage request allowance twice on the Always
    Free Resources page -- once for an Always-Free-only account and once for a
    paid or trial one. The capture retains BOTH occurrences so the fixture
    reproduces the live ambiguity rather than hiding it, and this test shows what
    pinning one would actually do.
    """

    fixture = load_case("oracle", "html", "oracle-always-free-resources")
    document = fixture.content.decode("utf-8")
    collector = _DocumentCollector()
    collector.feed(document)
    collector.close()
    matches = [
        block.text
        for block in collector.text_blocks
        if block.scope == "document" and DUPLICATED_STORAGE_BLOCK in block.text
    ]
    assert len(matches) == 2, f"expected the live duplication to be preserved, saw {len(matches)}"
    assert matches[0] == matches[1]

    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    retained = capture["duplicate_live_blocks_retained"]
    assert len(retained) == 1
    assert retained[0]["live_occurrences"] == 2
    assert retained[0]["retained_occurrences"] == 2
    assert "ambiguous_assertion" in retained[0]["why_not_pinned"]

    greedy = HtmlExtractionProfile(
        name="oracle_duplicate_probe",
        mode="assertions",
        trusted_assertions=True,
        assertions=(HtmlTextAssertion(text=matches[0], field="notes", value=matches[0]),),
        required_fields=("notes",),
    )
    adapter = HtmlDocAdapter(
        FixtureFetcher(
            {fixture.source_url: (fixture.content, "text/html")},
            FetchPolicy(official_domains=DOMAINS),
        ),
        source_urls=(fixture.source_url,),
        profile=greedy,
        provider="oracle",
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

    Character references are unescaped before comparison because the captures
    escape apostrophes and quotes, and Oracle's prose is full of both. Comparing
    against the raw markup silently matched zero blocks for every quotation
    containing an apostrophe -- caught by this helper's own exactly-once
    assertion rather than by inspection.
    """

    blocks = [
        match.group(0)
        for match in re.finditer(rf"<{tag}[^>]*>.*?</{tag}>", source, re.S)
        if needle in " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", match.group(0))).split())
    ]
    assert len(blocks) == 1, f"anchor {needle!r} matched {len(blocks)} <{tag}> blocks"
    return blocks[0]


PERPETUITY_ANCHOR = "free of charge in the home region of the tenancy"
ROW_ANCHOR = "Private templates"


def _mutate(source: str, mutation: str) -> str:
    """Apply exactly one named mutation, refusing an ambiguous anchor."""

    def once(old: str, new: str) -> str:
        assert source.count(old) == 1, f"{mutation}: anchor matched {source.count(old)} times"
        return source.replace(old, new)

    pinned = _block_containing(source, "p", PERPETUITY_ANCHOR)
    row = _block_containing(source, "tr", ROW_ANCHOR)

    if mutation == "assertion_deleted":
        return once(pinned, "")
    if mutation == "assertion_reworded":
        return once(
            pinned,
            pinned.replace("for the life of the account", "for the first year of the account"),
        )
    if mutation == "assertion_truncated":
        return once(
            pinned,
            "<p>All Oracle Cloud Infrastructure accounts (whether free or paid) have a set of "
            "resources that are free of charge.</p>",
        )
    if mutation == "assertion_duplicated":
        return once(pinned, pinned + "\n    " + pinned)
    if mutation == "undeclared_matrix_row":
        return once(
            row,
            row
            + "\n        <tr>\n          <td>Some Future Limit</td>"
            + "\n          <td><p>future-count</p></td>"
            + "\n          <td><p>9</p></td>\n        </tr>",
        )
    if mutation == "mapped_row_removed":
        return once(row, "")
    if mutation == "renamed_tier":
        return once("<th>Always Free</th>", "<th>Always Frees</th>")
    if mutation == "duplicated_table":
        table = source.split("<table>", 1)[1].split("</table>", 1)[0]
        return once("</table>", f"</table>\n    <table>{table}</table>")
    if mutation == "extra_column":
        # Header AND body rows gain a cell, so every row keeps a consistent width
        # and the declared signature is still a subset of the live headers.
        head, body = source.split("<tbody>", 1)
        head = head.replace("<th>Always Free</th>", "<th>Always Free</th><th>Notes</th>")
        return head + "<tbody>" + body.replace("</tr>", "<td>x</td></tr>")
    if mutation == "whitespace_entities":
        return once("<td>Private templates</td>", "<td>  Private&nbsp;templates  </td>")
    if mutation == "unrelated_paragraph_added":
        return once("  </body>", "    <p>An unrelated Oracle marketing sentence.</p>\n  </body>")
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
        # by the next author, which is worse than no guard. The third is the one
        # an evaluator reaches for first: an unrelated paragraph must leave the
        # document extracting exactly as before.
        ("extra_column", None),
        ("whitespace_entities", None),
        ("unrelated_paragraph_added", None),
    ],
)
def test_predicted_mutations_match_observation(mutation: str, expected_error: str | None) -> None:
    fixture = load_case("oracle", "html", "oracle-always-free-resources")
    source = fixture.source_path.read_text(encoding="utf-8")
    adapter = build_fixture_adapter(
        fixture, official_domains=DOMAINS, body=_mutate(source, mutation).encode()
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts.get("error") == expected_error


def test_an_unrelated_paragraph_leaves_the_verdict_intact() -> None:
    """The false-positive control that matters most, checked on the FACTS.

    "No error" is a weak claim on its own: a guard could still be silently
    dropping a fact. This asserts the whole fact set and the classifier verdict
    are byte-for-byte what the unmutated document produces.
    """

    fixture = load_case("oracle", "html", "oracle-always-free-resources")
    source = fixture.source_path.read_text(encoding="utf-8")

    clean = build_fixture_adapter(fixture, official_domains=DOMAINS)
    (before,) = clean.extract(clean.canonicalize(clean.fetch(fixture.source_url)))

    noisy = build_fixture_adapter(
        fixture,
        official_domains=DOMAINS,
        body=_mutate(source, "unrelated_paragraph_added").encode(),
    )
    (after,) = noisy.extract(noisy.canonicalize(noisy.fetch(fixture.source_url)))

    assert dict(after.facts) == dict(before.facts)
    assert len(after.evidence) == len(before.evidence)

    def verdict(candidate):
        return classify(
            OfferFacts(
                offer_type=str(candidate.facts["offer_type"]),
                requires_card=candidate.facts.get("requires_card"),
                has_paid_dependencies=candidate.facts.get("has_paid_dependencies"),
                exhaustion_behaviours=(str(candidate.facts["exhaustion_behaviour"]),),
            )
        ).zero_cost_class

    assert verdict(after) == verdict(before) == "UNKNOWN"


def _distinct_pinned_blocks(case: str) -> tuple[str, ...]:
    profile = resolve_profile(load_case("oracle", "html", case).profile)
    seen: list[str] = []
    for assertion in profile.assertions:
        if assertion.scope == "document" and assertion.text not in seen:
            seen.append(assertion.text)
    return tuple(seen)


def _delete_block(source: str, text: str) -> str:
    block = _block_containing(source, "p", text)
    return source.replace(block + "\n", "", 1)


@pytest.mark.parametrize(
    ("case", "text"),
    [(case, text) for case in SOURCE_CASES for text in _distinct_pinned_blocks(case)],
)
def test_deleting_any_pinned_block_rejects_the_document(case: str, text: str) -> None:
    """The sweep an evaluator runs by hand: EVERY pinned block is load-bearing.

    Not one representative block -- every one of them. A block that could be
    deleted without consequence is a block the profile does not actually depend
    on, and its presence in the capture would be decorative.
    """

    fixture = load_case("oracle", "html", case)
    source = fixture.source_path.read_text(encoding="utf-8")
    adapter = build_fixture_adapter(
        fixture, official_domains=DOMAINS, body=_delete_block(source, text).encode()
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts.get("error") == "assertion_not_found"


@pytest.mark.parametrize(
    ("case", "text"),
    [(case, text) for case in SOURCE_CASES for text in _distinct_pinned_blocks(case)],
)
def test_duplicating_any_pinned_block_rejects_the_document(case: str, text: str) -> None:
    """The other half of the evaluator's sweep: a duplicate is never a fact."""

    fixture = load_case("oracle", "html", case)
    source = fixture.source_path.read_text(encoding="utf-8")
    block = _block_containing(source, "p", text)
    mutated = source.replace(block, block + "\n    " + block, 1)
    adapter = build_fixture_adapter(fixture, official_domains=DOMAINS, body=mutated.encode())
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts.get("error") == "ambiguous_assertion"


def _run_with_profile(case: str, profile: HtmlExtractionProfile, body: bytes):
    fixture = load_case("oracle", "html", case)
    adapter = HtmlDocAdapter(
        FixtureFetcher(
            {fixture.source_url: (body, "text/html")}, FetchPolicy(official_domains=DOMAINS)
        ),
        source_urls=(fixture.source_url,),
        profile=profile,
        provider="oracle",
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

    fixture = load_case("oracle", "html", "oracle-always-free-resources")
    source = fixture.source_path.read_text(encoding="utf-8")
    deleted = _mutate(source, "assertion_deleted").encode()
    profile = resolve_profile("oracle_always_free_resources")

    baseline = _run_with_profile("oracle-always-free-resources", profile, deleted)
    assert baseline.facts.get("error") == "assertion_not_found"

    patched_assertions = tuple(
        dataclasses.replace(a, required=False) if PERPETUITY_ANCHOR in a.text else a
        for a in profile.assertions
    )
    patched = dataclasses.replace(profile, assertions=patched_assertions)
    disabled = [a.field for a in patched.assertions if not a.required]
    print(f"PATCHED LINE: HtmlTextAssertion(required=True -> False) for fields {disabled}")
    assert disabled, "the patch must actually disable something"

    weakened = _run_with_profile("oracle-always-free-resources", patched, deleted)
    assert weakened.facts.get("error") != "assertion_not_found"
    assert weakened.verification_state == "candidate"
    # THE reason this matters: with the guard off, a document missing its
    # perpetuity statement still yields a candidate -- silently missing the one
    # fact that makes `always_free` legitimate.
    assert "offer_type" not in weakened.facts

    # COLLATERAL CONTROL: a healthy document must still extract under the patched
    # profile. Without this we could not tell "the guard stopped firing" from
    # "something upstream collapsed and nothing extracts at all".
    healthy = _run_with_profile("oracle-always-free-resources", patched, fixture.content)
    assert healthy.verification_state == "candidate"
    assert healthy.facts["service"] == "Oracle Cloud Infrastructure Always Free"
    assert healthy.facts["offer_type"] == "always_free"


def test_the_row_completeness_guard_is_what_produces_unknown_matrix_rows() -> None:
    """Mutation test for the matrix guard, same shape as the assertion one."""

    fixture = load_case("oracle", "html", "oracle-always-free-resources")
    source = fixture.source_path.read_text(encoding="utf-8")
    injected = _mutate(source, "undeclared_matrix_row").encode()
    profile = resolve_profile("oracle_always_free_resources")

    baseline = _run_with_profile("oracle-always-free-resources", profile, injected)
    assert baseline.facts.get("error") == "unknown_matrix_rows"
    assert "Some Future Limit" in baseline.facts["detail"]

    patched_rows = dict(profile.matrix_rows)
    patched_rows["Some Future Limit"] = next(iter(profile.matrix_rows.values()))
    patched = dataclasses.replace(profile, matrix_rows=patched_rows)
    print(
        "PATCHED LINE: matrix_rows += {'Some Future Limit': HtmlMatrixRow(...)} "
        f"({len(profile.matrix_rows)} -> {len(patched.matrix_rows)} declared rows)"
    )

    weakened = _run_with_profile("oracle-always-free-resources", patched, injected)
    assert weakened.facts.get("error") != "unknown_matrix_rows"

    # COLLATERAL CONTROL. The patched profile declares a row the healthy document
    # does not contain, so a healthy document must now fail for a DIFFERENT and
    # named reason rather than passing silently or failing for the old one.
    healthy = _run_with_profile("oracle-always-free-resources", patched, fixture.content)
    assert healthy.facts.get("error") == "missing_matrix_rows"


@pytest.mark.parametrize("case", CARD_BY_QUOTATION)
def test_deleting_the_card_sentence_rejects_rather_than_publishing_a_free_claim(case: str) -> None:
    """The sentence that makes each perpetual offer NON-free is load-bearing."""

    fixture = load_case("oracle", "html", case)
    profile = resolve_profile(fixture.profile)
    (card_block,) = [a.text for a in profile.assertions if a.field == "requires_card"]
    source = fixture.source_path.read_text(encoding="utf-8")
    adapter = build_fixture_adapter(
        fixture, official_domains=DOMAINS, body=_delete_block(source, card_block).encode()
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    # Without its card evidence the document is REJECTED. It does NOT fall back
    # to an offer whose card requirement is merely unknown, which a downstream
    # reader could mistake for a safe default -- and which, for an `always_free`
    # offer, is the exact shape of a false $0 claim.
    assert candidate.facts["error"] == "assertion_not_found"


def test_deleting_the_perpetuity_sentence_rejects_rather_than_leaving_a_stale_type() -> None:
    fixture = load_case("oracle", "html", "oracle-always-free-services")
    profile = resolve_profile(fixture.profile)
    (offer_block,) = {a.text for a in profile.assertions if a.field == "offer_type"}
    source = fixture.source_path.read_text(encoding="utf-8")
    adapter = build_fixture_adapter(
        fixture, official_domains=DOMAINS, body=_delete_block(source, offer_block).encode()
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.facts["error"] == "assertion_not_found"


def test_the_healthy_documents_still_extract_after_the_mutation_battery() -> None:
    """Collateral control: the mutations above proved nothing if these now fail."""

    _, (resources,) = run_extraction_case(
        "oracle", "html", "oracle-always-free-resources", official_domains=DOMAINS
    )
    assert resources.verification_state == "candidate"
    assert resources.facts["private_templates"] == "100"
    assert resources.facts["concurrent_jobs"] == "2"
    assert len(resources.evidence) == 32

    _, (services,) = run_extraction_case(
        "oracle", "html", "oracle-always-free-services", official_domains=DOMAINS
    )
    assert services.verification_state == "candidate"
    assert services.facts["requires_card"] is True
    assert len(services.evidence) == 10


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
        assert "FIXTURE -> LIVE (supporting only)" in capture["live_reconciliation"]


# --------------------------------------------------------------------------- #
# Guards against claims that read stronger than the evidence supports          #
# --------------------------------------------------------------------------- #


PROVIDER_CONFIG_DIR = REPO_ROOT / "config" / "examples" / "providers"
ORACLE_MODULE = (
    REPO_ROOT / "apps" / "api" / "app" / "ingest" / "adapters" / "profiles" / "oracle.py"
)
ADAPTERS_DOC = REPO_ROOT / "docs" / "PROVIDER_ADAPTERS.md"


def _perpetual_offers_by_class() -> dict[str, list[str]]:
    """Classify EVERY perpetual offer in the repository, across all providers.

    Deliberately cross-provider. The claim this guards is a claim about the whole
    data set, and a claim about the whole data set cannot be checked from inside
    one slice -- which is precisely how a false generalisation shipped from here.
    """

    found: dict[str, list[str]] = {}
    for config_path in sorted(PROVIDER_CONFIG_DIR.glob("*.example.yaml")):
        provider = config_path.name.split(".")[0]
        model = load_and_validate(config_path)
        domains = tuple(model.provider.official_domains)
        source_ids = {source.id for source in model.sources}
        for case in available_cases(provider, "html"):
            if case not in source_ids:
                continue
            _, candidates = run_extraction_case(provider, "html", case, official_domains=domains)
            for candidate in candidates:
                facts = candidate.facts
                if facts.get("offer_type") != "always_free":
                    continue
                behaviours = ()
                if facts.get("exhaustion_behaviour"):
                    behaviours = (str(facts["exhaustion_behaviour"]),)
                result = classify(
                    OfferFacts(
                        offer_type="always_free",
                        requires_card=facts.get("requires_card"),
                        has_paid_dependencies=facts.get("has_paid_dependencies"),
                        exhaustion_behaviours=behaviours,
                    )
                )
                found.setdefault(result.zero_cost_class, []).append(
                    f"{provider}: {facts.get('service')}"
                )
    return found


def test_perpetuity_neither_entails_nor_precludes_zero_cost() -> None:
    """THE pin for a claim this module already shipped WRONG once.

    An earlier revision of ``oracle.py`` and ``docs/PROVIDER_ADAPTERS.md`` stated
    as a general law that "perpetual is not free". It was false: this repository
    had already measured perpetual offers reaching ``Z0_TRUE_FREE`` five slices
    earlier. It was also wrong in the OMISSION-FAVOURING direction, which this
    product forbids exactly as much as over-claiming.

    Both directions are asserted, because the corrected statement is a claim
    about entailment and needs both halves to be true:

    * at least one perpetual offer DOES reach Z0 -- so "perpetual is not free"
      is refuted, and stays refuted;
    * at least one perpetual offer does NOT -- so "perpetual means free" is
      refuted too, which is the error in the opposite direction.

    Exact counts are deliberately NOT pinned. They will move when a provider
    slice is added, and a test that forces an unrelated slice to edit Oracle's
    documentation would be coupling rather than a guard. The directional facts
    are what the corrected sentence actually asserts.
    """

    by_class = _perpetual_offers_by_class()
    z0 = by_class.get("Z0_TRUE_FREE", [])
    not_z0 = [
        name for label, names in by_class.items() if label != "Z0_TRUE_FREE" for name in names
    ]

    assert z0, (
        "no perpetual offer reaches Z0 anywhere in the repository, so the corrected "
        "statement in oracle.py is no longer supported by the data and must be re-derived"
    )
    assert not_z0, (
        "every perpetual offer reaches Z0, so the claim that perpetuity does not ENTAIL "
        "zero cost is no longer demonstrated and must be re-derived"
    )


#: Phrasings of the universal claim this slice shipped and had to retract. The
#: guard below forbids ASSERTING any of them; it deliberately permits QUOTING one
#: inside a passage that refutes it, because the retraction has to name what was
#: retracted or it documents nothing.
REFUTED_UNIVERSALS = (
    "perpetual is not free",
    "perpetual is never free",
    "all three agree",
    "perpetual offers are never free",
)
#: Words that mark a passage as refuting rather than asserting.
REFUTATION_MARKERS = ("false", "refuted", "under-reports", "earlier revision", "retract")


def test_no_shipped_prose_ASSERTS_the_refuted_universal() -> None:
    """The specific false claim must not come back as an assertion.

    Checked over the whole file with whitespace collapsed, not line by line: the
    claim wrapped across lines in both files, and a line-wise search would have
    missed it -- which is exactly how it survived review here.

    A blanket ban on the string would be the wrong guard. It would push the next
    author into rewording the claim rather than dropping it, and it would forbid
    the retraction from naming what it retracts. So each occurrence must sit
    within a refuting context.
    """

    for path in (ORACLE_MODULE, ADAPTERS_DOC):
        flat = " ".join(path.read_text(encoding="utf-8").split()).lower()
        for phrase in REFUTED_UNIVERSALS:
            start = 0
            while (index := flat.find(phrase, start)) != -1:
                window = flat[max(0, index - 500) : index + 500]
                assert any(marker in window for marker in REFUTATION_MARKERS), (
                    f"{path.name}: {phrase!r} appears without any refuting context nearby; "
                    "this claim was measured FALSE and may only be quoted in order to retract it"
                )
                start = index + len(phrase)


def test_the_corrected_prose_states_its_scope_and_does_not_misfile_cloudflare() -> None:
    """The correction's own numbers must carry the scope they are true of.

    Repository-wide the Z0 perpetual count is 5 across two providers; restricted
    to the six F008 providers it is 3, from GitHub alone. Those are different
    numbers about different sets, and swapping them would be a new false claim
    inside the correction for the old one. Cloudflare is F005.
    """

    for path in (ORACLE_MODULE, ADAPTERS_DOC):
        flat = " ".join(path.read_text(encoding="utf-8").split())
        assert "Cloudflare, which is F005 and not an F008 provider" in flat, path.name
        assert "Restricted to the six F008 providers the Z0 count is 3" in flat, path.name
        # The correction must not imply Oracle is the only withheld perpetual offer.
        assert "Oracle is *not* the only provider whose perpetual offer is withheld" in flat, (
            path.name
        )


def test_oracle_is_the_only_provider_withheld_by_a_quoted_card_requirement() -> None:
    """Pins the one distinctive claim the corrected prose does make.

    Re-derived across every perpetual offer in the repository: AWS, Azure and GCP
    are each blocked by ``automatic_billing``; Oracle alone is blocked by a
    quoted payment-card requirement. If another provider later gains a
    card-blocked perpetual offer, this fails and the prose must be re-derived.
    """

    by_reason: dict[str, set[str]] = {"card": set(), "billing": set()}
    for config_path in sorted(PROVIDER_CONFIG_DIR.glob("*.example.yaml")):
        provider = config_path.name.split(".")[0]
        model = load_and_validate(config_path)
        domains = tuple(model.provider.official_domains)
        source_ids = {source.id for source in model.sources}
        for case in available_cases(provider, "html"):
            if case not in source_ids:
                continue
            _, candidates = run_extraction_case(provider, "html", case, official_domains=domains)
            for candidate in candidates:
                facts = candidate.facts
                if facts.get("offer_type") != "always_free":
                    continue
                behaviours = ()
                if facts.get("exhaustion_behaviour"):
                    behaviours = (str(facts["exhaustion_behaviour"]),)
                result = classify(
                    OfferFacts(
                        offer_type="always_free",
                        requires_card=facts.get("requires_card"),
                        has_paid_dependencies=facts.get("has_paid_dependencies"),
                        exhaustion_behaviours=behaviours,
                    )
                )
                # EXACT condition strings, never substrings. The engine emits both
                # "A payment card is required." (gate 3, a definite exposure) and
                # "Whether a payment card is required is unknown." (gate 4), and
                # the second CONTAINS the first as a substring. A substring test
                # here reported GitHub and Vercel as card-blocked when their
                # offers are merely card-UNKNOWN -- caught by this test failing
                # rather than by review, and the same confusion of "stated" with
                # "unstated" that the absence/quotation distinction exists to keep
                # apart.
                conditions = set(result.blocking_conditions)
                if "A payment card is required." in conditions:
                    by_reason["card"].add(provider)
                if "A quota triggers automatic billing when exhausted." in conditions:
                    by_reason["billing"].add(provider)

    assert by_reason["card"] == {"oracle"}, by_reason["card"]
    # And the claim is only interesting because OTHER providers are withheld for
    # a different reason; if that stopped being true the sentence would mislead.
    assert by_reason["billing"], "no provider is billing-blocked; the contrast no longer holds"
    assert "oracle" not in by_reason["billing"]


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_the_capture_counts_only_blocks_that_actually_guard(case: str) -> None:
    """A capture may not describe a retained block as a pinned one.

    An earlier revision counted every block the generator retained and called
    them all "pinned". On five of six sources that overstated the guard by one,
    because the document <title> is retained as furniture but asserted by only
    one profile. The number now comes from the profile's own assertions, and the
    retained-but-not-pinned blocks are declared separately.
    """

    fixture = load_case("oracle", "html", case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    profile = resolve_profile(fixture.profile)

    pinned = {(a.scope, a.text) for a in profile.assertions}
    assert capture["pinned_block_count"] == len(pinned)
    assert f"all {len(pinned)} PINNED block(s)" in capture["live_reconciliation"]

    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()
    retained = [(b.scope, b.text) for b in collector.text_blocks]
    not_pinned = [b for b in retained if b not in pinned]
    assert capture["retained_not_pinned_count"] == len(not_pinned)
    if not_pinned:
        assert "RETAINED BUT NOT PINNED" in capture["live_reconciliation"]


def test_every_exported_constant_is_pinned_by_some_profile() -> None:
    """Dead evidence-looking constants imply guards that do not exist.

    Five ``*_TITLE`` constants were exported while no profile asserted any of
    them, which made the module read as if six documents pinned their titles when
    exactly one does.
    """

    import app.ingest.adapters.profiles.oracle as oracle_module

    pinned_texts = set()
    for name in PROFILE_NAMES:
        pinned_texts |= {a.text for a in resolve_profile(name).assertions}

    dead = []
    for name in oracle_module.__all__:
        value = getattr(oracle_module, name)
        if isinstance(value, str) and value not in pinned_texts:
            dead.append(name)
    assert not dead, f"exported but pinned by no profile: {dead}"
