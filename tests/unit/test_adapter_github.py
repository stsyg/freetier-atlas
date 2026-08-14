"""Contract tests for the GitHub OFFICIAL free-tier slice (F008 P1).

Offline, fixture-driven, no database, no network. These tests exist to make two
specific failure modes impossible to ship:

1. publishing a **time-limited offer as though it were perpetually free**;
2. publishing a **$0 claim that no longer has a source sentence behind it**.

The controls that matter here are:

1. :func:`test_every_published_number_appears_verbatim_in_the_captured_source`
   -- traceability. A number that is not present byte-for-byte in the captured
   official excerpt cannot be published, so no allowance can drift in from
   training data or from an editor's memory.
2. :func:`test_the_no_card_claim_is_pinned_to_verbatim_source_text` -- THE
   control this slice exists for. ``requires_card=False`` is the single fact
   that makes a Z0 verdict reachable. It is pinned to the verbatim sentence
   "If your account does not have a valid payment method on file, usage is
   blocked once you use up your quota." Rewording it, truncating it, or deleting
   it must REJECT the candidate rather than silently keep publishing $0.
3. :func:`test_the_enterprise_trial_is_not_z0_despite_requiring_no_card` -- the
   headline trap. The GitHub Enterprise Cloud trial asks for **no payment
   method** and still must never be Z0, because it expires after 30 days.
4. :func:`test_a_missing_material_condition_is_unknown_never_assumed_free` --
   unknown is better than guessed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from app.classify.engine import OfferFacts, classify
from app.config import load_and_validate
from app.ingest import CandidateFacts, resolve_profile
from app.ingest.adapters.html import _DocumentCollector, _header_row
from app.ingest.adapters.profiles.github import NO_PAYMENT_METHOD_BLOCKS_USAGE

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

#: The captures whose LIVE page publishes a real allowance table, so the profile
#: is a matrix re-derived from that table's own headers.
MATRIX_CASES = (
    "github-actions-billing",
    "github-packages-billing",
    "github-codespaces-billing",
)

#: The captures whose live page publishes NO table at all: every fact comes from
#: pinned prose, and the profile declares no table selector.
ASSERTION_ONLY_CASES = (
    "github-pages-limits",
    "github-enterprise-cloud-trial",
)

#: The captures whose live page carries the no-payment-method sentence.
NO_CARD_CASES = MATRIX_CASES

#: ``(case, plan row label)`` for the blank-pivoted-cell mutation.
_EMPTIED_CELL_CASES = (
    ("github-actions-billing", "GitHub Free", "minutes_per_month_github_free"),
    ("github-packages-billing", "GitHub Free", "data_transfer_per_month_github_free"),
    (
        "github-codespaces-billing",
        "GitHub Free for personal accounts",
        "compute_time_per_month_github_free_for_personal_accounts",
    ),
)

_CELL = re.compile(r"<t[dh][^>]*>.*?</t[dh]>", re.DOTALL)


def _flex(sentence: str) -> re.Pattern[str]:
    """Match ``sentence`` across any whitespace, since Prettier reflows prose."""

    return re.compile(r"\s+".join(re.escape(word) for word in sentence.split()))


def _blank_pivoted_cell(source: str, plan_label: str, tier_index: int) -> str:
    """Empty the pivoted cell of the row labelled ``plan_label``."""

    label_at = source.index(f">{plan_label}<")
    row_start = source.rindex("<tr>", 0, label_at)
    row_end = source.index("</tr>", label_at)
    row = source[row_start:row_end]
    cells = list(_CELL.finditer(row))
    assert len(cells) > tier_index, f"row for {plan_label!r} has too few cells"
    target = cells[tier_index]
    mutated_row = row[: target.start()] + "<td></td>" + row[target.end() :]
    return source[:row_start] + mutated_row + source[row_end:]


def _tier_index(case: str, profile) -> int:
    capture = json.loads(
        (load_case(PROVIDER, ADAPTER, case).directory / "capture.json").read_text(encoding="utf-8")
    )
    headers = [h.strip().lower() for h in capture["structure"]["headers"]]
    return headers.index(profile.matrix_tier_header.strip().lower())


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
    """The `partial` case deletes the perpetuity sentence: REJECTED, not free.

    The page still publishes the allowance table, so a reader that trusted the
    table alone would happily go on calling the offer always-free. Because
    ``offer_type`` is pinned to a verbatim sentence instead, its disappearance
    rejects the whole document. A page that stops publishing whether an offer is
    perpetual can never be read as "perpetual by default".
    """

    fixture, candidates = run_extraction_case(
        PROVIDER, ADAPTER, "partial", official_domains=("docs.github.com",)
    )
    (candidate,) = candidates
    assert candidate.verification_state == "rejected"
    assert candidate.facts["error"] == "assertion_not_found"
    # Not merely unknown: the fact is absent entirely, so nothing downstream can
    # read a default out of it.
    assert "offer_type" not in candidate.facts
    problems = list(
        build_fixture_adapter(fixture, official_domains=("docs.github.com",)).validate(candidate)
    )
    assert problems and "assertion_not_found" in problems[0]


@pytest.mark.parametrize(("case", "plan_label", "field"), _EMPTIED_CELL_CASES)
def test_an_emptied_allowance_cell_is_unknown_and_never_a_guessed_number(
    case: str, plan_label: str, field: str
) -> None:
    """Mutation: blank the free plan's pivoted cell and prove nothing is invented.

    This is the surviving half of the old drop-a-column control: the value must
    come back UNKNOWN (``None``), never as a remembered or neighbouring number.
    """

    fixture = load_case(PROVIDER, ADAPTER, case)
    profile = resolve_profile(fixture.profile)
    text = fixture.source_path.read_text(encoding="utf-8")
    want = fixture.expected_candidates[0]["facts"]
    assert want[field], "the control is vacuous if the cell was already empty"

    mutated = _blank_pivoted_cell(text, plan_label, _tier_index(case, profile))
    assert mutated != text, f"{case}: mutation did not change the document"

    adapter = build_fixture_adapter(
        fixture, official_domains=("docs.github.com",), body=mutated.encode("utf-8")
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert isinstance(candidate, CandidateFacts)
    assert candidate.facts[field] is None, f"{case}: {field} was invented rather than left unknown"
    # The identity still comes from the pinned prose, so the row is traceable.
    assert candidate.facts["service"] == want["service"]
    # And the neighbouring plans are untouched: nothing shifted into the gap.
    for key, value in want.items():
        if key != field and key.startswith(field.rsplit("_github", 1)[0]):
            assert candidate.facts[key] == value


@pytest.mark.parametrize("case", OFFICIAL_CASES)
def test_deleting_a_pinned_block_rejects_the_document(case: str) -> None:
    """Every pinned block is load-bearing: delete any one and the document fails."""

    fixture = load_case(PROVIDER, ADAPTER, case)
    profile = resolve_profile(fixture.profile)
    text = fixture.source_path.read_text(encoding="utf-8")

    checked = 0
    for assertion in profile.assertions:
        if assertion.scope != "document":
            continue
        mutated = _flex(assertion.text).sub("", text, count=1)
        assert mutated != text, f"{case}: could not delete {assertion.text[:40]!r}"
        adapter = build_fixture_adapter(
            fixture, official_domains=("docs.github.com",), body=mutated.encode("utf-8")
        )
        (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
        assert candidate.verification_state == "rejected", (
            f"{case}: deleting {assertion.field!r}'s source block still produced a candidate"
        )
        assert candidate.facts["error"] == "assertion_not_found"
        checked += 1
    assert checked >= 1, f"{case}: no pinned block was checked; the control is vacuous"


@pytest.mark.parametrize("case", MATRIX_CASES)
def test_an_undeclared_matrix_row_rejects_the_document(case: str) -> None:
    """A plan GitHub adds later must reject, never be silently dropped."""

    fixture = load_case(PROVIDER, ADAPTER, case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    width = len(capture["structure"]["headers"])
    text = fixture.source_path.read_text(encoding="utf-8")

    extra = "<tr>" + "".join(f"<td>Undeclared {i}</td>" for i in range(width)) + "</tr>"
    mutated = text.replace("</tbody>", extra + "</tbody>", 1)
    assert mutated != text

    adapter = build_fixture_adapter(
        fixture, official_domains=("docs.github.com",), body=mutated.encode("utf-8")
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.verification_state == "rejected"
    assert candidate.facts["error"] == "unknown_matrix_rows", candidate.facts


# --- The claim this slice exists to defend ---------------------------------


@pytest.mark.parametrize("case", NO_CARD_CASES)
@pytest.mark.parametrize("mutation", ("reworded", "truncated", "deleted"))
def test_the_no_card_claim_is_pinned_to_verbatim_source_text(case: str, mutation: str) -> None:
    """Reword, truncate or delete the no-card sentence -- each must REJECT.

    ``requires_card=False`` is the single fact that makes a Z0 verdict reachable
    for these offers. Before this slice it was read out of a table cell, so the
    claim survived any change to the sentence that justified it. Now the sentence
    itself is load-bearing: PREDICTION, recorded before measurement, is that all
    three mutations yield ``assertion_not_found``.
    """

    fixture = load_case(PROVIDER, ADAPTER, case)
    text = fixture.source_path.read_text(encoding="utf-8")
    pinned = _flex(NO_PAYMENT_METHOD_BLOCKS_USAGE)
    assert pinned.search(text), "the fixture lost the pinned sentence"

    if mutation == "reworded":
        mutated = pinned.sub(
            "If your account does not have a valid payment method on file, usage is paused "
            "once you use up your quota.",
            text,
            count=1,
        )
    elif mutation == "truncated":
        mutated = pinned.sub(
            "If your account does not have a valid payment method on file.", text, count=1
        )
    else:
        mutated = pinned.sub("", text, count=1)
    assert mutated != text, f"{case}/{mutation}: mutation did not change the document"

    adapter = build_fixture_adapter(
        fixture, official_domains=("docs.github.com",), body=mutated.encode("utf-8")
    )
    (candidate,) = adapter.extract(adapter.canonicalize(adapter.fetch(fixture.source_url)))
    assert candidate.verification_state == "rejected", (
        f"{case}/{mutation}: the no-card claim survived a change to its own source sentence"
    )
    assert candidate.facts["error"] == "assertion_not_found"
    assert "requires_card" not in candidate.facts


def test_the_unmutated_fixtures_still_publish_the_no_card_fact() -> None:
    """Positive control: the mutation test above is not passing vacuously."""

    for case in NO_CARD_CASES:
        _, (candidate,) = run_extraction_case(
            PROVIDER, ADAPTER, case, official_domains=("docs.github.com",)
        )
        assert candidate.facts["requires_card"] is False
        assert candidate.facts["exhaustion_behaviour"] == "hard_stop"


# --- Capture integrity -----------------------------------------------------


@pytest.mark.parametrize("case", OFFICIAL_CASES)
def test_capture_structure_matches_every_retained_target_cell(case: str) -> None:
    """`capture.json` must describe the committed table exactly, with no silent cuts.

    Where the live page publishes no table the capture must hold none either --
    a capture that invents structure the live page does not have is a worse
    fixture, not a more convenient one.
    """

    fixture = load_case(PROVIDER, ADAPTER, case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    collector = _DocumentCollector()
    collector.feed(fixture.content.decode("utf-8"))
    collector.close()

    if not capture["live_table_present"]:
        assert collector.tables == [], (
            f"{case}: the live page has no table, so the capture must contain none -- "
            "synthesizing an anchor table to satisfy the extractor makes the capture "
            "misrepresent its own source"
        )
        assert capture["structure"]["headers"] == []
        assert capture["structure"]["rows"] == []
    else:
        assert len(collector.tables) == 1, f"{case}: the excerpt must hold exactly one table"
        header_index, header = _header_row(collector.tables[0])
        assert header_index is not None, f"{case}: {header}"
        assert list(header.cells) == capture["structure"]["headers"]
        assert [list(r.cells) for r in collector.tables[0].rows[header_index + 1 :]] == capture[
            "structure"
        ]["rows"]
    assert capture["target_table_rows_removed"] == []
    assert capture["target_table_cells_removed"] == []
    assert capture["ignored_extraction_rows"] == []


@pytest.mark.parametrize("case", OFFICIAL_CASES)
def test_asserted_blocks_match_the_pinned_capture_hashes(case: str) -> None:
    fixture = load_case(PROVIDER, ADAPTER, case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    profile = resolve_profile(fixture.profile)
    actual = [
        hashlib.sha256(assertion.text.encode("utf-8")).hexdigest()
        for assertion in profile.assertions
    ]
    assert actual == capture["structure"]["asserted_block_sha256"]


@pytest.mark.parametrize("case", OFFICIAL_CASES)
def test_a_table_less_live_page_is_disclosed_and_claims_nothing(case: str) -> None:
    """Where the live page has no table, the profile must declare no table at all.

    The stronger form of the old control. Previously these two captures carried
    a fabricated one-cell anchor table, constrained to map no column so it made
    no claim -- but it existed nowhere on the live page, so the profile returned
    ``table_not_found`` against the real document. Now the profile is
    assertion-only and reads no table, so there is nothing to synthesize and
    nothing to constrain.
    """

    fixture = load_case(PROVIDER, ADAPTER, case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    profile = resolve_profile(fixture.profile)
    if capture["live_table_present"]:
        assert profile.mode == "matrix"
        assert profile.header_signature
        return

    assert profile.mode == "assertions"
    assert profile.assertions, "an assertion-only profile with no assertions proves nothing"
    # Not merely "maps no column": declares no table machinery whatsoever, so it
    # cannot quietly reacquire a synthetic anchor.
    assert profile.table_id is None
    assert profile.table_class is None
    assert profile.header_signature == ()
    assert dict(profile.columns) == {}
    assert dict(profile.matrix_rows) == {}
    assert "ZERO <table>" in capture["live_table_note"]


@pytest.mark.parametrize("case", ASSERTION_ONLY_CASES)
def test_an_assertion_only_capture_contains_no_fabricated_table(case: str) -> None:
    """The fabricated anchor table is gone from the committed bytes, and stays gone."""

    fixture = load_case(PROVIDER, ADAPTER, case)
    source = fixture.source_path.read_text(encoding="utf-8")
    assert "<table" not in source.lower(), (
        f"{case}: the live page has zero <table> elements, so the capture must too"
    )
    assert "captured-source-anchor" not in source


@pytest.mark.parametrize("case", ASSERTION_ONLY_CASES)
def test_an_assertion_only_profile_extracts_from_a_document_with_no_tables(case: str) -> None:
    """The feature: a table-free official page extracts, with per-fact evidence.

    This is what the fabricated anchor was standing in for. Against the captured
    bytes -- which now contain no table, exactly like the live page -- the
    profile must produce a valid candidate whose every fact carries its own
    pinned-block evidence location.
    """

    fixture, (candidate,) = run_extraction_case(
        PROVIDER, ADAPTER, case, official_domains=("docs.github.com",)
    )
    assert candidate.verification_state == "candidate"
    assert "error" not in candidate.facts
    assert candidate.facts == dict(fixture.expected_candidates[0]["facts"])
    # One evidence location per asserted fact: nothing is published unsourced.
    profile = resolve_profile(fixture.profile)
    assert len(candidate.evidence) == len(profile.assertions)
    assert all("assertion[" in (e.selector or "") for e in candidate.evidence)


@pytest.mark.parametrize("case", OFFICIAL_CASES)
def test_every_duplicated_live_block_is_disclosed(case: str) -> None:
    """A sentence that repeats on the live page must be declared, not quietly cut."""

    fixture = load_case(PROVIDER, ADAPTER, case)
    capture = json.loads((fixture.directory / "capture.json").read_text(encoding="utf-8"))
    disclosed = capture["duplicate_live_blocks_not_retained"]
    source = fixture.source_path.read_text(encoding="utf-8")
    for entry in disclosed:
        assert entry["live_occurrences"] > entry["retained_in_capture"]
        assert entry["retained_in_capture"] == len(_flex(entry["text"]).findall(source))
        assert "ambiguous_assertion" in entry["note"]


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
    assert profile.required_fields == ("service", "offer_type")
    assert profile.trusted_assertions
    asserted = {assertion.field for assertion in profile.assertions}
    # The identity and every material Z0 gate are pinned to verbatim source text
    # rather than read out of an author-populated cell.
    assert {"service", "offer_type", "requires_card", "has_paid_dependencies"} <= asserted
    assert "exhaustion_behaviour" in asserted
    assert not set(profile.columns) & asserted, (
        f"{name}: a pinned fact must not also be readable from a table column"
    )


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
    """The synthetic conflicting row demands a card and bills -> Z1, never Z0.

    This case is read with the pre-existing GENERIC ``quota_document`` profile.
    The GitHub profiles now pin ``requires_card`` to a verbatim sentence, which
    makes two rows of one GitHub page structurally incapable of disagreeing about
    it -- so an intra-page disagreement can only be expressed generically.
    """

    fixture = load_case(PROVIDER, ADAPTER, "contradictory")
    assert fixture.profile == "quota_document"
    first, second = (dict(c["facts"]) for c in fixture.expected_candidates[:2])

    # Both rows claim the same offer identity, so a reader that trusted either
    # one in isolation would publish a different answer for the same offer.
    assert (first["service"], first["offer_type"]) == (second["service"], second["offer_type"])
    assert first["requires_card"] != second["requires_card"]

    def verdict(facts: dict[str, object]) -> str:
        return classify(
            OfferFacts(
                offer_type=str(facts["offer_type"]),
                requires_card=bool(facts["requires_card"]),
                has_paid_dependencies=bool(facts["has_paid_dependencies"]),
                exhaustion_behaviours=tuple(str(q) for q in facts["quotas"]),
            )
        ).zero_cost_class

    assert verdict(first) == "Z0_TRUE_FREE"
    assert verdict(second) == "Z1_BILLING_EXPOSURE"


def test_a_contradiction_about_perpetuity_is_structurally_undetectable() -> None:
    """The most dangerous disagreement is the one the reconciler cannot report.

    ``_identity_of`` groups candidates by ``(provider, service, offer_type)``
    while ``MATERIAL_FACT_FIELDS`` also lists ``offer_type``. Two official
    sources that disagree about whether an offer is perpetual or a trial
    therefore land in *different* identity groups and are never compared, so
    the exact §A0 conflict -- always_free versus trial -- can never raise a
    contradiction. This test pins that behaviour so the day someone fixes it
    is a deliberate, visible change rather than a silent one.
    """

    from app.ingest.reconcile import MATERIAL_FACT_FIELDS, _identity_of

    assert "offer_type" in MATERIAL_FACT_FIELDS
    perpetual = {"service": "GitHub Actions", "offer_type": "always_free"}
    temporary = {"service": "GitHub Actions", "offer_type": "trial"}
    assert _identity_of("github", perpetual) != _identity_of("github", temporary), (
        "offer_type is part of the identity key, so it cannot also be compared as a conflict"
    )


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
