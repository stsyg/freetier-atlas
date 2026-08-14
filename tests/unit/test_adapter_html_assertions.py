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
