"""What an ambiguous source cell must mean on the material Z0 gate.

WHY THIS FILE EXISTS
--------------------
``requires_card`` and ``has_paid_dependencies`` are the two facts that decide
``Z0_TRUE_FREE`` versus ``Z1_BILLING_EXPOSURE``. Both are bool-coerced columns,
and ``app.ingest.adapters._common.to_bool`` returns ``None`` for a cell that is
neither a clear yes nor a clear no.

Measured on this tree (2026-08-25) by two independent line tracers over the
whole suite, with and without a database: the ``return None`` fall-through in
``to_bool`` had **never been executed**. A provider rewording one table cell --
"Card required: Depends on your plan" -- reaches it.

The classification engine already honours the tri-state (``test_z0_classifier``
covers it). What no test covered is the BRIDGE: that an ambiguous cell actually
*arrives* at the engine as ``None`` rather than being coerced to ``False``
somewhere between the table cell and ``OfferFacts``. ``False`` there does not
mean "unknown" -- it means "no card required", which is an unsupported claim
that a service is free. That is the failure this file exists to prevent, so it
drives the whole chain rather than unit-testing either end of it.

The rule being enforced is AGENTS.md, "Data rules": *a failed or unknown
material Z0 condition prevents Z0 classification*, and *unknown is better than
guessed*.

Every arm is paired. Asserting only "ambiguous is not Z0" would pass just as
well against an engine that never returns Z0 at all, so the clean-"No" arm
asserts that Z0 IS still reachable and the clean-"Yes" arm asserts that a real
billing exposure is still caught.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.classify import UNKNOWN, Z0_TRUE_FREE, Z1_BILLING_EXPOSURE, OfferFacts, classify
from app.ingest import FetchPolicy, FixtureFetcher, HtmlDocAdapter, resolve_profile
from app.ingest.adapters._common import to_bool

_URL = "https://provider.example/free-tier"
_AS_OF = date(2026, 1, 15)

#: Wordings a real pricing page uses when the answer is not a clean yes or no.
#: None of these may ever be read as "no card required".
AMBIGUOUS_CELLS = (
    "Depends on your plan",
    "Only for some regions",
    "Contact sales",
    "See terms",
    "N/A",
    "Sometimes",
)


def _document(*, card: str, paid: str, exhaustion: str = "hard_stop") -> str:
    """One ordinary free-tier table, with the two material cells substituted."""

    return (
        "<!doctype html><html><head><title>Free tier</title></head><body>"
        '<table id="free-tier">'
        "<thead><tr>"
        "<th>Service</th><th>Offer type</th><th>Card required</th>"
        "<th>Paid dependencies</th><th>Exhaustion</th>"
        "</tr></thead>"
        f"<tbody><tr><td>Widgets</td><td>always_free</td><td>{card}</td>"
        f"<td>{paid}</td><td>{exhaustion}</td></tr></tbody>"
        "</table></body></html>"
    )


def _extract_facts(html: str) -> dict:
    fetcher = FixtureFetcher(
        {_URL: (html.encode(), "text/html")},
        FetchPolicy(official_domains=("provider.example",)),
    )
    adapter = HtmlDocAdapter(fetcher, (_URL,), resolve_profile("quota_document"))
    document = adapter.canonicalize(adapter.fetch(_URL))
    candidates = list(adapter.extract(document))
    assert len(candidates) == 1, f"expected one candidate row, got {len(candidates)}"
    return dict(candidates[0].facts)


def _classify(facts: dict):
    """Carry the extracted facts onto the Z0 gate exactly as the publisher does."""

    return classify(
        OfferFacts(
            offer_type=facts["offer_type"],
            requires_card=facts["requires_card"],
            has_paid_dependencies=facts["has_paid_dependencies"],
            exhaustion_behaviours=facts["quotas"] or (),
        ),
        as_of=_AS_OF,
    )


# --- the positive control: Z0 must still be reachable -----------------------


def test_a_clean_no_still_reaches_z0() -> None:
    """POSITIVE CONTROL for every arm below.

    Without this, "ambiguous is not Z0" would be satisfied by an engine that
    never says Z0 at all, and the negative arms would prove nothing.
    """

    facts = _extract_facts(_document(card="No", paid="No"))
    assert facts["requires_card"] is False
    assert facts["has_paid_dependencies"] is False

    result = _classify(facts)
    assert result.zero_cost_class == Z0_TRUE_FREE
    assert result.blocking_conditions == ()


def test_a_clean_yes_is_still_caught_as_billing_exposure() -> None:
    """NEGATIVE CONTROL: a real card requirement must still be Z1, not UNKNOWN.

    An implementation that answered UNKNOWN to everything would pass the
    ambiguity arms while quietly losing the ability to name a billing exposure.
    """

    facts = _extract_facts(_document(card="Yes", paid="No"))
    assert facts["requires_card"] is True

    result = _classify(facts)
    assert result.zero_cost_class == Z1_BILLING_EXPOSURE
    assert any("card" in reason.lower() for reason in result.reasons)


# --- the untested path -----------------------------------------------------


@pytest.mark.parametrize("cell", AMBIGUOUS_CELLS)
def test_an_ambiguous_card_cell_is_unknown_and_never_free(cell: str) -> None:
    """An ambiguous card cell must block Z0 -- and must not read as "no card"."""

    assert to_bool(cell) is None, f"{cell!r} was resolved to a boolean rather than UNKNOWN"

    facts = _extract_facts(_document(card=cell, paid="No"))
    # The distinction that matters: None (unknown) is NOT False (known safe).
    assert facts["requires_card"] is None
    assert facts["requires_card"] is not False

    result = _classify(facts)
    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert any("card" in condition.lower() for condition in result.blocking_conditions), (
        f"an unknown card requirement was not named as a blocking condition: "
        f"{result.blocking_conditions!r}"
    )


@pytest.mark.parametrize("cell", AMBIGUOUS_CELLS)
def test_an_ambiguous_paid_dependency_cell_is_unknown_and_never_free(cell: str) -> None:
    """The same rule on the second material field, which shares the coercion."""

    assert to_bool(cell) is None

    facts = _extract_facts(_document(card="No", paid=cell))
    assert facts["has_paid_dependencies"] is None
    assert facts["has_paid_dependencies"] is not False

    result = _classify(facts)
    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE
    assert any("paid" in condition.lower() for condition in result.blocking_conditions)


def test_a_blank_material_cell_is_unknown_not_false() -> None:
    """An EMPTY cell is the cheapest way for a page edit to reach this path.

    ``_coerce`` short-circuits a blank cell to ``None`` before ``to_bool`` is
    consulted, so this is a second, independent route to the same UNKNOWN and it
    must not be allowed to arrive as ``False`` either.
    """

    facts = _extract_facts(_document(card="", paid=""))
    assert facts["requires_card"] is None
    assert facts["has_paid_dependencies"] is None

    result = _classify(facts)
    assert result.zero_cost_class == UNKNOWN
    assert result.zero_cost_class != Z0_TRUE_FREE


def test_to_bool_recognises_the_unambiguous_spellings_both_ways() -> None:
    """The coercion is only useful if it still resolves what IS unambiguous.

    Paired directly against the ambiguity arms: this is the control proving
    ``to_bool`` has not simply been reduced to "always UNKNOWN", which would
    make every arm above pass for the wrong reason.
    """

    for spelling in ("Yes", "true", "REQUIRED", "y", "1"):
        assert to_bool(spelling) is True, spelling
    for spelling in ("No", "false", "Not required", "none", "n", "0"):
        assert to_bool(spelling) is False, spelling
    assert to_bool(None) is None
