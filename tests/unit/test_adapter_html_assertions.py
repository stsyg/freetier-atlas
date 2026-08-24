"""Assertion-only HTML profiles, and the EXPLICIT evidence floor they require.

Some official pages state their free-tier terms entirely in prose and contain no
table at all. Before this module's feature existed the extraction engine could
not emit a candidate without one, so shipping such a page meant committing a
*fabricated* anchor table into the fixture -- structure that existed nowhere on
the live page, and which made the profile return ``table_not_found`` against the
real document. ``mode="assertions"`` removes the reason to fabricate anything.

The dangerous half of that change is the reason this module exists. The
mandatory matrix was doing a second job nobody declared: it was an ACCIDENTAL
EVIDENCE FLOOR. A profile that proved nothing could not emit a candidate,
because it could not select a table. Making the matrix optional dissolves that
accident, and in this product a candidate backed by no evidence is a potential
unsupported claim that a service is free -- a far worse defect than refusing to
extract.

So the floor is now EXPLICIT and stated per mode, and
:func:`test_a_profile_that_declares_no_evidence_at_all_is_rejected` is the most
important test in the slice. It is deliberately not a fall-through: this
repository has already been bitten once by a lookup returning ``None`` being
read as "no constraint applies, therefore allow", which silently exempted
exactly the fields that most needed checking. A mode that is not listed in
:data:`~app.ingest.adapters.html._FACT_SOURCES_BY_MODE` raises ``ValueError``
rather than defaulting to permitted.
"""

from __future__ import annotations

import pytest
from app.ingest import (
    FetchPolicy,
    FixtureFetcher,
    HtmlColumn,
    HtmlDocAdapter,
    HtmlExtractionProfile,
    HtmlMatrixRow,
    HtmlTextAssertion,
)

URL = "https://example.com/limits"

#: A page shaped like the real ones this feature exists for: prose only, and not
#: a single ``<table>`` element anywhere in it.
TABLE_FREE_HTML = (
    "<!doctype html><html><head><title>Widget limits - Example Docs</title></head><body>"
    "<h1>Widget limits</h1>"
    "<p>Widget is included at no cost on every account.</p>"
    "<p>If your account has no payment method, usage stops instead of billing.</p>"
    "<ul><li>Published widgets may be no larger than 1 GB.</li></ul>"
    "</body></html>"
)


def _assertions() -> tuple[HtmlTextAssertion, ...]:
    return (
        HtmlTextAssertion(
            text="Widget limits - Example Docs",
            field="service",
            value="Widget",
            scope="title",
        ),
        HtmlTextAssertion(
            text="Widget is included at no cost on every account.",
            field="offer_type",
            value="always_free",
        ),
        HtmlTextAssertion(
            text="If your account has no payment method, usage stops instead of billing.",
            field="requires_card",
            value=False,
        ),
        HtmlTextAssertion(
            text="If your account has no payment method, usage stops instead of billing.",
            field="exhaustion_behaviour",
            value="hard_stop",
        ),
        HtmlTextAssertion(
            text="Published widgets may be no larger than 1 GB.",
            field="published_size",
            value="1 GB",
        ),
    )


def _assertion_profile(
    *,
    assertions: tuple[HtmlTextAssertion, ...] | None = None,
    **overrides: object,
) -> HtmlExtractionProfile:
    return HtmlExtractionProfile(
        name="assertion_only_test",
        mode="assertions",
        trusted_assertions=True,
        assertions=_assertions() if assertions is None else assertions,
        **overrides,  # type: ignore[arg-type]
    )


def _extract(html: str, profile: HtmlExtractionProfile):
    fetcher = FixtureFetcher(
        {URL: (html.encode(), "text/html")},
        FetchPolicy(official_domains=("example.com",)),
    )
    adapter = HtmlDocAdapter(fetcher, (URL,), profile, provider="example")
    document = adapter.canonicalize(adapter.fetch(URL))
    return adapter, list(adapter.extract(document))


def _assert_rejected(candidates, error: str):
    assert len(candidates) == 1
    assert candidates[0].verification_state == "rejected"
    assert candidates[0].facts["error"] == error


# --- THE EVIDENCE FLOOR ----------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "kwargs"),
    (
        # Row mode: a table is selected, but nothing maps out of it and nothing
        # is pinned. It would emit one empty candidate per body row.
        ("rows", {"table_id": "free-tier"}),
        # Row mode with no selector at all: the emptiest profile expressible.
        ("rows", {}),
        # Matrix mode: headers declared, but no row maps to a fact.
        (
            "matrix",
            {
                "header_signature": ("Metric", "Free"),
                "matrix_metric_header": "Metric",
                "matrix_tier_header": "Free",
            },
        ),
        # Assertion mode: the new mode, declaring no assertions.
        ("assertions", {}),
    ),
)
def test_a_profile_that_declares_no_evidence_at_all_is_rejected(
    mode: str, kwargs: dict[str, object]
) -> None:
    """THE central guard of this slice: no evidence declared, no profile.

    Making the matrix optional must not make "no evidence at all" acceptable.
    Every one of these profiles is syntactically fine and semantically empty:
    each could only ever emit a candidate backed by nothing. Construction must
    refuse them, so the failure happens at import time in front of an author
    rather than at publication time in front of a user.

    PREDICTION, recorded before measurement: all four raise ``ValueError`` and
    none is constructible.
    """

    with pytest.raises(ValueError, match="declares no source of facts"):
        HtmlExtractionProfile(name="empty", mode=mode, **kwargs)  # type: ignore[arg-type]


def test_the_evidence_floor_is_a_per_mode_decision_not_a_fall_through() -> None:
    """A field that is inert in the selected mode is not evidence.

    ``matrix_rows`` are only read in matrix mode. A row-mode profile that
    declares them has still declared no way to read a fact, so a permissive
    "any of these fields is set" floor would wave it through. The floor is
    keyed by mode precisely so it cannot.
    """

    with pytest.raises(ValueError, match="declares no source of facts"):
        HtmlExtractionProfile(
            name="inert-source",
            mode="rows",
            table_id="free-tier",
            matrix_rows={"CPU": HtmlMatrixRow("cpu")},
        )


def test_an_unknown_mode_is_refused_rather_than_defaulting_to_permitted() -> None:
    """An unlisted mode must not skip the floor by not being in the table."""

    with pytest.raises(ValueError, match="HTML profile mode must be one of"):
        HtmlExtractionProfile(name="bogus", mode="freeform", assertions=_assertions())


@pytest.mark.parametrize(
    "sources",
    (
        {"columns": {"service": HtmlColumn("service", "text")}},
        {"trusted_assertions": True, "assertions": _assertions()},
    ),
)
def test_one_declared_source_of_facts_is_enough_to_clear_the_floor(
    sources: dict[str, object],
) -> None:
    """Positive control: the floor rejects emptiness, not profiles in general."""

    profile = HtmlExtractionProfile(name="fine", table_id="free-tier", **sources)  # type: ignore[arg-type]
    assert profile.name == "fine"


def test_matrix_mode_still_requires_its_mapped_rows() -> None:
    """Relaxing the matrix requirement globally must not relax it within matrix mode."""

    with pytest.raises(ValueError, match="declares no source of facts"):
        HtmlExtractionProfile(
            name="matrix-without-rows",
            mode="matrix",
            header_signature=("Metric", "Free"),
            matrix_metric_header="Metric",
            matrix_tier_header="Free",
            trusted_assertions=True,
            assertions=_assertions(),
        )


# --- Assertion-only profiles declare no table ------------------------------


@pytest.mark.parametrize(
    "table_field",
    (
        {"table_id": "anchor"},
        {"table_class": "anchor"},
        {"header_signature": ("Captured source",)},
        {"columns": {"service": HtmlColumn("service", "text")}},
        {"matrix_rows": {"CPU": HtmlMatrixRow("cpu")}},
        {"matrix_metric_header": "Metric"},
        {"matrix_tier_header": "Free"},
        {"ignored_matrix_rows": ("Notes",)},
    ),
)
def test_an_assertion_only_profile_may_not_declare_any_table_machinery(
    table_field: dict[str, object],
) -> None:
    """ "Assertion-only" must not be able to quietly become "table-backed" again.

    This is the guard against regrowing the fabricated anchor: if a table
    selector were merely ignored in this mode, an author could reintroduce one
    and the fixture would drift away from the live page again.
    """

    with pytest.raises(ValueError, match="read no table and must declare none"):
        _assertion_profile(**table_field)


# --- The feature -----------------------------------------------------------


def test_an_assertion_only_profile_extracts_from_a_document_with_no_tables() -> None:
    """The feature: a table-free page yields a full candidate with per-fact evidence."""

    assert "<table" not in TABLE_FREE_HTML.lower(), "the control is vacuous with a table present"

    adapter, candidates = _extract(TABLE_FREE_HTML, _assertion_profile())
    (candidate,) = candidates

    assert candidate.verification_state == "candidate"
    assert candidate.facts == {
        "service": "Widget",
        "offer_type": "always_free",
        "requires_card": False,
        "exhaustion_behaviour": "hard_stop",
        "published_size": "1 GB",
    }
    # Every fact carries its own pinned-block provenance; nothing is unsourced.
    assert len(candidate.evidence) == 5
    assert all("assertion[" in (location.selector or "") for location in candidate.evidence)
    assert list(adapter.validate(candidate)) == []


def test_an_assertion_only_profile_reads_prose_only_and_never_a_stray_table() -> None:
    """A table on the page contributes nothing: the profile declared none."""

    with_table = TABLE_FREE_HTML.replace(
        "</body>",
        "<table><thead><tr><th>Plan</th></tr></thead>"
        "<tbody><tr><td>Enterprise</td></tr></tbody></table></body>",
    )
    _, (candidate,) = _extract(with_table, _assertion_profile())

    assert candidate.verification_state == "candidate"
    assert "Enterprise" not in candidate.facts.values()
    assert candidate.facts["service"] == "Widget"
    assert len(candidate.evidence) == 5


# --- Fail-closed at extraction time ----------------------------------------


def test_an_assertion_only_extraction_with_no_evidence_is_rejected() -> None:
    """The RUNTIME half of the floor: nothing matched, so nothing is emitted.

    A profile whose assertions are all optional clears the construction-time
    floor legitimately, but a document in which none of them matches would
    otherwise produce a candidate with no facts and no evidence. That must
    reject rather than pass silently as "nothing to object to".
    """

    optional = tuple(
        HtmlTextAssertion(
            text=assertion.text,
            field=assertion.field,
            value=assertion.value,
            scope=assertion.scope,
            required=False,
        )
        for assertion in _assertions()
    )
    profile = _assertion_profile(assertions=optional, required_fields=())
    adapter, candidates = _extract(
        "<!doctype html><html><head><title>Unrelated</title></head>"
        "<body><p>Nothing pinned here.</p></body></html>",
        profile,
    )

    _assert_rejected(candidates, "no_assertion_evidence")
    assert list(adapter.validate(candidates[0])), "a rejected candidate must be flagged"


def test_a_deleted_pinned_block_rejects_an_assertion_only_document() -> None:
    """`assertion_not_found` still fires with no table anywhere in the document."""

    mutated = TABLE_FREE_HTML.replace(
        "<p>If your account has no payment method, usage stops instead of billing.</p>", ""
    )
    assert mutated != TABLE_FREE_HTML

    _, candidates = _extract(mutated, _assertion_profile())
    _assert_rejected(candidates, "assertion_not_found")
    assert "requires_card" not in candidates[0].facts


@pytest.mark.parametrize(
    "mutation",
    (
        "If your account has no payment method, usage pauses instead of billing.",
        "If your account has no payment method.",
        "If your account has no payment method, usage stops instead of billing. Terms apply.",
    ),
    ids=("reworded", "truncated", "appended-clause"),
)
def test_a_drifted_pinned_block_rejects_an_assertion_only_document(mutation: str) -> None:
    """Whole-block equality: reword, truncate or append and the claim dies with it."""

    mutated = TABLE_FREE_HTML.replace(
        "If your account has no payment method, usage stops instead of billing.", mutation
    )
    assert mutated != TABLE_FREE_HTML

    _, candidates = _extract(mutated, _assertion_profile())
    _assert_rejected(candidates, "assertion_not_found")


def test_a_duplicated_pinned_block_rejects_an_assertion_only_document() -> None:
    """`ambiguous_assertion` still fires without a table to disambiguate against."""

    duplicated = TABLE_FREE_HTML.replace(
        "</body>", "<p>Widget is included at no cost on every account.</p></body>"
    )
    _, candidates = _extract(duplicated, _assertion_profile())
    _assert_rejected(candidates, "ambiguous_assertion")


def test_closed_field_vocabularies_still_bind_in_assertion_only_mode() -> None:
    """Construction-time vocabulary validation is not skipped by the new mode."""

    with pytest.raises(ValueError, match="Assertion field 'offer_type' requires one of"):
        _assertion_profile(
            assertions=(
                HtmlTextAssertion(
                    text="Widget is included at no cost on every account.",
                    field="offer_type",
                    value="free_forever",
                ),
            )
        )


def test_assertion_only_extraction_is_deterministic() -> None:
    """Identical bytes yield identical facts and identical evidence."""

    _, first = _extract(TABLE_FREE_HTML, _assertion_profile())
    _, second = _extract(TABLE_FREE_HTML, _assertion_profile())
    assert first == second


# --- RULE 1: the assertion field must be REGISTERED ------------------------
#
# The defect this closes was a fall-through, not an omission. Value validation
# was `_ASSERTION_CLOSED_VALUES.get(field)` followed by `if allowed is not
# None`, so a field name the vocabulary did not know skipped validation
# ENTIRELY -- silently, which is the mode this repository has already been
# bitten by once (see this module's docstring). It matters because nothing
# downstream reserves such a name either: `app.publish.revalidate` treats every
# unreserved fact key as a QUOTA METRIC, so a mistyped material condition was
# not merely unvalidated, it was republished as a quota row while the condition
# it resembles stayed absent. Two failures, neither of them loud.

PINNED_BLOCK = "If your account has no payment method, usage stops instead of billing."


def _one(field: str, value: object, *, text: str = PINNED_BLOCK) -> tuple[HtmlTextAssertion, ...]:
    return (HtmlTextAssertion(text=text, field=field, value=value),)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        # Near-misses of real material conditions. Each is a well-formed metric
        # NAME, so shape alone would have waved it through -- and then the
        # publisher would republish it as a quota row while the material
        # condition it resembles stayed absent.
        ("exhaustion_behavior", "hard_stop"),
        ("requires_cards", False),
        ("offer_typ", "always_free"),
        ("offer_types", "always_free"),
        ("servic", "Widget"),
        ("note", "anything"),
        # Shapes that are not metric names at all.
        ("Requires Card", "no"),
        ("offer type", "always_free"),
        ("", "anything"),
        ("_private", "anything"),
        ("CARD", "no"),
        ("card-required", "no"),
    ),
    ids=(
        "us-spelling-of-exhaustion",
        "pluralised-requires-card",
        "truncated-offer-type",
        "pluralised-offer-type",
        "truncated-service",
        "singular-notes",
        "spaced-and-capitalised",
        "spaced",
        "empty",
        "leading-underscore",
        "shouting",
        "hyphenated",
    ),
)
def test_an_unregistered_assertion_field_is_refused_at_construction(
    field: str, value: object
) -> None:
    """REJECT half of the pair. Not a warning, not a skip -- a hard failure.

    PREDICTION, recorded before measurement: every case raises ``ValueError``,
    and the message names BOTH the offending field and the registered
    vocabulary, so the author is told what to write instead rather than merely
    that they are wrong.
    """

    with pytest.raises(ValueError) as excinfo:
        _assertion_profile(assertions=_one(field, value))

    message = str(excinfo.value)
    assert repr(field) in message, "the message must name the offending field"
    assert "registered vocabulary" in message, "the message must name the vocabulary"
    # A representative reserved field and the quota-metric shape both appear, so
    # the author can see the two things a field is allowed to be.
    assert "exhaustion_behaviour" in message
    assert "quota metric" in message


def test_the_near_miss_guard_names_what_the_author_probably_meant() -> None:
    """A typo message that does not say the right spelling teaches nothing.

    MEASURED before the threshold was chosen: across all 206 distinct
    non-reserved field names registered by every profile in this repository,
    ZERO are within edit distance 2 of any reserved name -- so distance 1 has a
    measured margin of 2 against real work rather than being a guess.
    """

    with pytest.raises(ValueError) as excinfo:
        _assertion_profile(assertions=_one("exhaustion_behavior", "hard_stop"), required_fields=())

    message = str(excinfo.value)
    assert "'exhaustion_behavior'" in message, "must name what was written"
    assert "'exhaustion_behaviour'" in message, "must name what was probably meant"


def test_a_quota_metric_that_is_not_confusable_is_still_accepted() -> None:
    """PAIRED CONTROL for the near-miss guard.

    The guard must reject typos, not metric names in general. These are real
    names taken from the merged providers; if the confusability threshold is
    ever widened, this is what goes red first.
    """

    for metric in (
        "outbound_data_transfer",
        "free_state_transitions_per_month",
        "heatwave_backup_storage",
        "published_site_size",
        "notifications",
    ):
        profile = _assertion_profile(assertions=_one(metric, "some value"), required_fields=())
        assert profile.assertions[0].field == metric


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("service", "Widget"),
        ("offer_type", "always_free"),
        ("eligibility", "Hobby plan"),
        ("requires_card", False),
        ("has_paid_dependencies", True),
        ("exhaustion_behaviour", "hard_stop"),
        ("commercial_use_allowed", True),
        ("personal_use_allowed", False),
        # An open quota metric: 89 of these exist across the six merged
        # providers, so the vocabulary must admit them by shape, not by list.
        ("published_site_size", "1 GB"),
        ("minutes_per_month_github_free", "2,000"),
    ),
)
def test_a_registered_assertion_field_is_accepted(field: str, value: object) -> None:
    """PAIRED CONTROL for the test above.

    Without this, every rejection could be a profile failing for some unrelated
    reason -- an empty-evidence floor, a bad scope, anything. These differ from
    the rejected cases ONLY in the field name and value.
    """

    profile = _assertion_profile(assertions=_one(field, value), required_fields=())
    assert profile.assertions[0].field == field
    assert profile.assertions[0].value == value


@pytest.mark.parametrize("field", ("error", "detail", "provider", "quotas"))
def test_the_adapter_control_plane_can_never_be_asserted(field: str) -> None:
    """A profile must not be able to forge or mask a rejection.

    ``HtmlDocAdapter._rejected`` writes ``{"error": ..., "detail": ...}`` and
    ``validate()`` treats any candidate carrying ``error`` as rejected. A
    profile able to pin those names could make a healthy document look rejected,
    or -- far worse in this product -- be used to shape what a rejection says.
    ``provider`` is identity and ``quotas`` is the structured list the publisher
    builds itself.
    """

    with pytest.raises(ValueError, match="control plane"):
        _assertion_profile(assertions=_one(field, "anything"), required_fields=())


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("requires_card", "no"),
        ("requires_card", 0),
        ("requires_card", 1),
        ("has_paid_dependencies", "false"),
        ("commercial_use_allowed", "yes"),
        ("service", 42),
        ("service", ""),
        ("eligibility", "   "),
        # A quota metric carrying a non-string: it would be str()-ed into the
        # quota's raw text ("True") by the publisher.
        ("published_site_size", True),
        ("published_site_size", 1),
        ("published_site_size", None),
    ),
)
def test_a_registered_field_asserted_with_the_wrong_kind_of_value_is_refused(
    field: str, value: object
) -> None:
    """Registration binds the VALUE too, not only the name.

    ``0`` is not ``False`` here on purpose: the type check is exact, so a value
    that merely compares equal to a boolean cannot become a material Z0 gate.
    """

    with pytest.raises(ValueError):
        _assertion_profile(assertions=_one(field, value), required_fields=())


# --- RULE 2: free text is QUOTED, never composed ---------------------------
#
# docs/PROVIDER_ADAPTERS.md: "Free-text values that reach the UI, such as
# `notes`, must reproduce the asserted source wording verbatim rather than
# paraphrase it." That was prose. It is machinery now.
#
# SCOPE, stated here so its limits cannot be discovered later. The rule binds
# fields whose value reaches the UI as prose -- `notes`, `display_name`,
# `service_description`. It deliberately does NOT bind:
#
#   * closed-vocabulary fields (`offer_type`, `exhaustion_behaviour`, the
#     boolean gates), which are MAPPINGS onto a canonical value and are already
#     validated against their own vocabularies;
#   * `service`, `eligibility` and `documentation_url`, which are canonical
#     identities, not quotations. MEASURED: ten of the six merged providers'
#     `service` assertions map a canonical name ("AWS 12 Month Free Tier") onto
#     a block that does not contain it, and that is correct -- requiring
#     containment there would be a false rejection on correct work;
#   * quota metrics, whose values are re-derived into an amount and a unit by
#     `parse_quantity` rather than shown as prose. MEASURED: three of them
#     normalise a spelled-out quantity ("exactly one free database" -> "1").
#
# The boundary is the documented one. Widening it would fail correct work, and
# a guard that reds on correct work gets deleted rather than fixed.

FREE_TEXT_BLOCK = (
    "Vercel sends you notifications as you approach your usage quotas. "
    "You will not be charged for any additional usage."
)


@pytest.mark.parametrize(
    ("label", "value"),
    (
        ("paraphrased", "Vercel notifies you as you near your quotas and never charges you."),
        ("reworded-one-word", FREE_TEXT_BLOCK.replace("notifications", "warnings")),
        ("composed-from-two-blocks", FREE_TEXT_BLOCK + " Usage above the quota is billed."),
        ("summarised", "No charge for additional usage."),
        ("re-typed-with-a-typo", FREE_TEXT_BLOCK.replace("quotas", "quotos")),
        ("whitespace-not-normalised", FREE_TEXT_BLOCK.replace(". ", ".  ")),
    ),
)
def test_a_composed_free_text_value_is_refused_at_construction(label: str, value: str) -> None:
    """REJECT half of the pair: a `notes` value that is not in its own block.

    PREDICTION, recorded before measurement: all six raise. The last two matter
    most and are the least obvious -- a re-typed sentence with a single-letter
    slip, and one whose whitespace was not normalised the way the extractor
    normalises it, are exactly how a "quotation" stops being one. Transcription
    is where a composed quotation creeps in.
    """

    assert value != FREE_TEXT_BLOCK, f"the {label} case is not actually different"
    with pytest.raises(ValueError, match="QUOTED from the block"):
        _assertion_profile(
            assertions=_one("notes", value, text=FREE_TEXT_BLOCK),
            required_fields=(),
        )


@pytest.mark.parametrize(
    ("label", "value"),
    (
        ("the whole block", FREE_TEXT_BLOCK),
        ("a leading clause", "Vercel sends you notifications as you approach your usage quotas."),
        ("a trailing clause", "You will not be charged for any additional usage."),
        ("a phrase", "will not be charged"),
    ),
)
def test_the_genuine_quotation_is_accepted(label: str, value: str) -> None:
    """PAIRED CONTROL for the test above.

    Truncating a quotation is still quoting it, so a clause of the pinned block
    passes. Containment rather than whole-block equality is the deliberate
    choice: equality would reject a legitimate partial quotation, which is a
    false positive on correct work.
    """

    profile = _assertion_profile(
        assertions=_one("notes", value, text=FREE_TEXT_BLOCK),
        required_fields=(),
    )
    assert profile.assertions[0].value == value


def test_the_verbatim_rule_survives_the_profile_normalising_its_own_block() -> None:
    """Verbatim is measured against the representation the extractor SEES.

    ``__post_init__`` runs ``normspace`` over ``text``, so a block declared with
    ragged whitespace is compared in its collapsed form -- the same form
    ``_apply_assertions`` compares at runtime. A value quoted from the RAW
    spelling must therefore fail, and one quoted from the NORMALISED spelling
    must pass. Getting this backwards is how a guard produces false rejections
    on correct work.
    """

    ragged = "You  will   not\nbe charged for any additional usage."
    normalised = "You will not be charged for any additional usage."

    with pytest.raises(ValueError, match="QUOTED from the block"):
        _assertion_profile(
            assertions=_one("notes", "You  will   not be charged", text=ragged),
            required_fields=(),
        )

    profile = _assertion_profile(
        assertions=_one("notes", "will not be charged", text=ragged),
        required_fields=(),
    )
    assert profile.assertions[0].text == normalised


def test_a_mapped_field_is_not_held_to_the_quotation_rule() -> None:
    """The documented boundary, pinned so it cannot be widened by accident.

    ``service`` maps a canonical identity onto a block; ten of the six merged
    providers' service assertions do exactly this. If this test ever fails, the
    quotation rule has been widened onto mapped fields and every merged provider
    is about to go red.
    """

    profile = _assertion_profile(
        assertions=(
            HtmlTextAssertion(
                text="Setting up a trial of GitHub Enterprise Cloud - GitHub Enterprise Cloud Docs",
                field="service",
                value="GitHub Enterprise Cloud trial",
                scope="title",
            ),
        ),
        required_fields=(),
    )
    assert profile.assertions[0].value == "GitHub Enterprise Cloud trial"


def test_both_new_rules_apply_in_every_mode_not_only_assertion_only_mode() -> None:
    """A matrix profile's assertions are held to the same two rules.

    The checks live in ``__post_init__``, not in the assertion-only branch, so a
    table-backed profile cannot smuggle an unregistered field or a composed
    quotation past them by declaring a header signature.
    """

    common: dict[str, object] = {
        "name": "matrix_rules_probe",
        "mode": "matrix",
        "header_signature": ("Metric", "Free"),
        "matrix_metric_header": "Metric",
        "matrix_tier_header": "Free",
        "matrix_rows": {"CPU": HtmlMatrixRow("cpu")},
        "trusted_assertions": True,
    }

    with pytest.raises(ValueError, match="registered vocabulary"):
        HtmlExtractionProfile(assertions=_one("exhaustion_behavior", "hard_stop"), **common)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="QUOTED from the block"):
        HtmlExtractionProfile(
            assertions=_one("notes", "not in the block", text=FREE_TEXT_BLOCK),
            **common,  # type: ignore[arg-type]
        )

    # Paired control: the same matrix profile with compliant assertions builds.
    profile = HtmlExtractionProfile(
        assertions=_one("exhaustion_behaviour", "hard_stop"),
        **common,  # type: ignore[arg-type]
    )
    assert profile.mode == "matrix"
