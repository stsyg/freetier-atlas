"""Both ``conflicting_assertion`` shapes, and the accepting arm beside each.

WHY THIS FILE EXISTS
--------------------
``HtmlDocAdapter._apply_assertions`` refuses a document whenever a pinned
assertion disagrees with a value the same document already yielded. It carries
TWO such refusals:

* **A** -- ``if assertion.field in facts and existing != assertion.value`` --
  the assertion disagrees with a value already extracted, e.g. from a table cell;
* **B** -- ``if assertion.field in asserted_fields and asserted_fields[...] !=
  assertion.value`` -- two pinned assertions disagree with each other.

``tests/unit/test_adapter_html_matrix.py::test_conflicting_assertions_fail_closed``
covers the OUTCOME. Measured at 6e14a471 by mutation over the whole tree (3006
tests, green first): disabling **either** branch on its own left all 3006 green.
Neither is independently asserted, because the one covered input satisfies both
predicates at once and each branch masks the other.

Instrumented directly -- the two ``detail`` strings were temporarily tagged and
both shapes driven -- the answer is that **branch A answers both**, so branch B
never fires on any input this engine can construct: after an accepted assertion
``facts[field]`` and ``asserted_fields[field]`` are written together and can
never diverge, so ``B`` implies ``A`` and ``A`` is evaluated first.

That is REPORTED here, not repaired. Removing a redundant refusal from a
provider adapter changes what an evidence-extraction path rejects, which is an
owner decision in a mandatory Level-2 category.

What this file adds is the missing measurement: the **table-vs-assertion** input
shape, which no test in the tree drove at all, pinned in both directions.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.ingest import (
    FetchPolicy,
    FixtureFetcher,
    HtmlColumn,
    HtmlDocAdapter,
    HtmlExtractionProfile,
    HtmlTextAssertion,
)

URL = "https://example.com/pricing"
HTML_SOURCE = (
    Path(__file__).resolve().parents[2] / "apps" / "api" / "app" / "ingest" / "adapters" / "html.py"
)

CONFLICT_ERROR = "conflicting_assertion"


def _page(body: str) -> str:
    return (
        f"<!doctype html><html><head><title>Conflict Probe</title></head><body>{body}</body></html>"
    )


def _table(offer_cell: str) -> str:
    return (
        '<table class="offers"><thead><tr><th>Service</th><th>Offer</th></tr></thead>'
        f"<tbody><tr><td>Widgets</td><td>{offer_cell}</td></tr></tbody></table>"
    )


def _row_profile(*assertions: HtmlTextAssertion) -> HtmlExtractionProfile:
    """A ROW-mode profile whose table supplies ``offer_type`` directly.

    Row mode is what makes branch A reachable at all: the candidate already
    carries ``offer_type`` from a cell before a single assertion is applied, so
    ``asserted_fields`` is still empty when the first conflict is judged.
    """

    return HtmlExtractionProfile(
        name="conflict_rows",
        table_class="offers",
        columns={"Service": HtmlColumn("service"), "Offer": HtmlColumn("offer_type")},
        required_fields=(),
        trusted_assertions=bool(assertions),
        assertions=assertions,
    )


def _assertions_profile(*assertions: HtmlTextAssertion) -> HtmlExtractionProfile:
    return HtmlExtractionProfile(
        name="conflict_assertions",
        mode="assertions",
        required_fields=(),
        trusted_assertions=True,
        assertions=assertions,
    )


def _extract(html: str, profile: HtmlExtractionProfile):
    fetcher = FixtureFetcher(
        {URL: (html.encode(), "text/html")},
        FetchPolicy(official_domains=("example.com",)),
    )
    adapter = HtmlDocAdapter(fetcher, (URL,), profile, provider="example")
    document = adapter.canonicalize(adapter.fetch(URL))
    return list(adapter.extract(document))


# --- the refusing direction --------------------------------------------------


def test_a_table_cell_and_an_assertion_that_disagree_reject_the_document() -> None:
    """The shape no test in the tree drove: a cell says trial, the prose says free.

    This is the wrongly-ACCEPT direction that matters most on this product. If it
    were accepted, the assertion would overwrite the cell and a trial offer would
    be republished as ``always_free`` with pinned evidence attached to it -- an
    unsupported free claim carrying provenance, which is the one thing the
    product forbids.
    """

    candidates = _extract(
        _page(_table("trial") + "<p>Free plan</p>"),
        _row_profile(HtmlTextAssertion(text="Free plan", field="offer_type", value="always_free")),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.verification_state == "rejected"
    assert candidate.facts["error"] == CONFLICT_ERROR
    assert "offer_type" in candidate.facts["detail"]
    # The rejected candidate keeps no extracted facts, so a caller cannot read
    # the overwritten value out of it by mistake.
    assert "offer_type" not in {k for k in candidate.facts if k not in {"error", "detail"}}


def test_two_assertions_that_disagree_on_one_field_reject_the_document() -> None:
    candidates = _extract(
        _page("<p>Free plan</p><p>Trial plan</p>"),
        _assertions_profile(
            HtmlTextAssertion(text="Free plan", field="offer_type", value="always_free"),
            HtmlTextAssertion(text="Trial plan", field="offer_type", value="trial"),
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].verification_state == "rejected"
    assert candidates[0].facts["error"] == CONFLICT_ERROR


# --- the permitting direction (without which the above proves nothing) -------


def test_a_table_cell_and_an_assertion_that_AGREE_are_accepted() -> None:
    """Identical profile and identical page but for the cell's value.

    A refusal test alone passes just as well against an adapter that rejects
    every document carrying both a table and an assertion. The two arms here
    differ in exactly one character sequence -- the cell text -- so the delta
    between them is the conflict predicate and nothing else.
    """

    candidates = _extract(
        _page(_table("always_free") + "<p>Free plan</p>"),
        _row_profile(HtmlTextAssertion(text="Free plan", field="offer_type", value="always_free")),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.verification_state == "candidate"
    assert "error" not in candidate.facts
    assert candidate.facts["offer_type"] == "always_free"
    assert candidate.facts["service"] == "Widgets"
    # The agreeing assertion still contributes its own pinned evidence location,
    # so agreement is not silently treated as "nothing to record".
    assert any("assertion[0]" in (loc.selector or "") for loc in candidate.evidence)


def test_an_assertion_on_a_field_the_table_does_not_supply_is_accepted() -> None:
    """No prior value means no conflict: the assertion simply adds its fact."""

    candidates = _extract(
        _page(_table("always_free") + "<p>No credit card required</p>"),
        _row_profile(
            HtmlTextAssertion(text="No credit card required", field="requires_card", value=False)
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].verification_state == "candidate"
    assert candidates[0].facts["requires_card"] is False
    assert candidates[0].facts["offer_type"] == "always_free"


def test_the_two_conflict_shapes_report_the_identical_error_string() -> None:
    """One wording for one property, compared against itself rather than a literal."""

    table_shape = _extract(
        _page(_table("trial") + "<p>Free plan</p>"),
        _row_profile(HtmlTextAssertion(text="Free plan", field="offer_type", value="always_free")),
    )[0]
    assertion_shape = _extract(
        _page("<p>Free plan</p><p>Trial plan</p>"),
        _assertions_profile(
            HtmlTextAssertion(text="Free plan", field="offer_type", value="always_free"),
            HtmlTextAssertion(text="Trial plan", field="offer_type", value="trial"),
        ),
    )[0]

    assert table_shape.facts["error"] == assertion_shape.facts["error"] == CONFLICT_ERROR


# --- structure, not phrase ---------------------------------------------------


def test_every_conflict_refusal_in_apply_assertions_uses_one_error_string() -> None:
    """Parsed from the AST, so a docstring or comment cannot satisfy this.

    Both refusals are currently unreachable *independently* -- branch B is
    dominated by branch A -- which means a reworded copy would be invisible to
    every behavioural test in the tree, including the two above. Comparing the
    literals the source actually passes is the only instrument that still sees
    drift in a branch nothing can execute.

    This asserts the CURRENT source, and changes nothing about what the adapter
    matches, accepts or rejects.
    """

    tree = ast.parse(HTML_SOURCE.read_text(encoding="utf-8"))
    apply_assertions = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_assertions"
    )

    errors: list[str] = []
    for call in ast.walk(apply_assertions):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_rejected"):
            continue
        # _rejected(document, provider, error, detail) -- the third positional.
        if len(call.args) >= 3 and isinstance(call.args[2], ast.Constant):
            errors.append(call.args[2].value)

    # Vacuity floor: an AST walk that found nothing must fail loudly rather than
    # report a confident all-clear over an empty list.
    assert len(errors) >= 4, (
        f"only {len(errors)} literal _rejected() error codes found in _apply_assertions; "
        "the AST walk stopped seeing them, so this assertion is vacuous"
    )
    assert errors.count(CONFLICT_ERROR) == 2, (
        "expected exactly two conflict refusals in _apply_assertions; the pair is "
        f"mutually masking, so drift in either is otherwise silent. Found: {errors!r}"
    )
    assert "assertion_not_found" in errors
    assert "ambiguous_assertion" in errors
