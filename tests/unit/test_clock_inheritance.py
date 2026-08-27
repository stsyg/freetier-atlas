"""The clock a function uses must be the one its caller was holding.

This file guards a defect class rather than a single bug: a function that takes
an OPTIONAL clock and, when not given one, invents ``datetime.now(UTC)`` or
``date.today()``. The invented value is the most plausible default imaginable,
which is exactly what makes it dangerous -- it is correct-looking, in the wrong
frame of reference, and nothing anywhere raises.

These tests deliberately assert the CAPABILITY ("no call site can obtain a
second clock") rather than the SYMPTOM ("the clock helper is called once"). The
distinction is not academic. The guard that missed the ``coverage_signal_context``
defect asserted the symptom, and the full suite stayed green while a router built
one response payload from two different moments.

A signature check is the right instrument for a capability claim because it
constrains every call site that will ever exist. A behavioural test constrains
only the call site somebody wrote today.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path

import pytest
from app.adviser.abuse import admin as abuse_admin
from app.adviser.select import build_candidate, build_pool, gather_candidates
from app.classify.engine import OfferFacts, classify
from app.classify.orm import classify_offer
from app.ingest.reconcile_coverage import find_coverage_mismatches

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every function converted from an invented clock to an inherited one, with the
#: name of the parameter that must now be supplied.
PARTICIPANTS = [
    pytest.param(classify, "as_of", id="classify"),
    pytest.param(classify_offer, "as_of", id="classify_offer"),
    pytest.param(build_candidate, "as_of", id="build_candidate"),
    pytest.param(build_pool, "as_of", id="build_pool"),
    pytest.param(gather_candidates, "now", id="gather_candidates"),
    pytest.param(find_coverage_mismatches, "now", id="find_coverage_mismatches"),
]

#: Genuine boundaries. Someone must source the clock and these are where the
#: process begins, so their optional parameter is CORRECT and is locked here so
#: a later tidy-up cannot convert a real entry point into a participant.
ENTRY_POINTS = [
    pytest.param(abuse_admin.set_kill_switch, "now", id="set_kill_switch"),
    pytest.param(abuse_admin.reset_breaker, "now", id="reset_breaker"),
]


@pytest.mark.parametrize(("func", "param"), PARTICIPANTS)
def test_a_participant_cannot_be_called_without_a_clock(func, param) -> None:
    """The parameter is REQUIRED, so omitting it is impossible rather than silent.

    Enforced at runtime as a ``TypeError``: there is no static type checker on
    the Python side of this repository (``requirements-dev.txt`` carries ruff,
    pytest, detect-secrets, pip-audit, httpx and cryptography -- no mypy, no
    pyright), so claiming a static gate here would be claiming a gate that does
    not exist.
    """

    sig = inspect.signature(func)
    assert param in sig.parameters, f"{func.__name__} no longer takes {param!r}"
    assert sig.parameters[param].default is inspect.Parameter.empty, (
        f"{func.__name__}({param}=...) has regained a default. A default here means a "
        f"caller can omit it and silently receive a SECOND moment, different from the "
        f"one its caller is using, with the whole suite still green."
    )


@pytest.mark.parametrize(("func", "param"), ENTRY_POINTS)
def test_an_entry_point_keeps_its_optional_clock(func, param) -> None:
    """These two are boundaries and must NOT be converted for symmetry.

    ``main()`` in the abuse admin CLI is the process boundary: an operator typing
    a command has no moment to pass, and each command is a single self-contained
    write with no sibling clock-consumer to disagree with. Converting them would
    break the CLI and buy nothing. This test exists so that a future sweep of
    this defect class stops here deliberately instead of by accident.
    """

    sig = inspect.signature(func)
    assert sig.parameters[param].default is None, (
        f"{func.__name__} is an ENTRY POINT; its optional {param!r} is correct. "
        f"See the note above its definition before changing this."
    )


def _facts(**kw) -> OfferFacts:
    return OfferFacts(
        offer_type="always_free",
        requires_card=False,
        has_paid_dependencies=False,
        exhaustion_behaviours=("hard_stop",),
        **kw,
    )


def test_classify_actually_consumes_as_of_rather_than_merely_accepting_it() -> None:
    """A required parameter that is ignored is no better than an invented one.

    The observable effect of the clock is the availability reason. Measured, and
    stated narrowly on purpose: ``as_of`` does NOT change the zero-cost class --
    a non-null ``available_until`` yields Z2 on either side of the boundary. What
    it changes is the published sentence, and one of the two sentences says a
    closed window is still open.
    """

    closes = date(2029, 12, 31)
    still_open = classify(_facts(available_until=closes), as_of=date(2029, 6, 1))
    expired = classify(_facts(available_until=closes), as_of=date(2030, 6, 1))

    assert still_open.zero_cost_class == expired.zero_cost_class, (
        "as_of is not supposed to move the class; if this fails the defect is "
        "larger than the audit measured and the report must be corrected."
    )
    assert any("bounded availability window" in r for r in still_open.reasons)
    assert any("availability ended" in r for r in expired.reasons)
    assert still_open.reasons != expired.reasons


def test_classify_is_deterministic_for_frozen_facts() -> None:
    """Its docstring promises identical inputs produce identical output.

    Before this change that promise was false: ``classify(facts)`` read
    ``date.today()`` and returned different reasons on different days from the
    same frozen facts.
    """

    facts = _facts(available_until=date(2029, 12, 31))
    first = classify(facts, as_of=date(2030, 6, 1))
    second = classify(facts, as_of=date(2030, 6, 1))
    assert first == second


# --- the source-level guard --------------------------------------------------
# A signature can be required today and quietly re-optionalised tomorrow by a
# well-meaning refactor that "restores convenience". This walks the actual source
# of the converted modules and fails on the fallback SHAPE, so the defect cannot
# come back under a different parameter name.

_AUDITED_MODULES = (
    "apps/api/app/classify/engine.py",
    "apps/api/app/classify/orm.py",
    "apps/api/app/adviser/select.py",
    "apps/api/app/ingest/reconcile_coverage.py",
)

_INVENTIONS = {"now", "utcnow", "today"}


def _invents_a_moment(node: ast.AST) -> bool:
    """``X.now(...)`` / ``X.utcnow()`` / ``date.today()`` in a defaulting position."""

    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _INVENTIONS
    )


@pytest.mark.parametrize("rel", _AUDITED_MODULES)
def test_an_audited_module_never_defaults_a_clock_it_was_not_given(rel: str) -> None:
    """No ``x = x or datetime.now(...)`` / ``x if x else date.today()`` remains.

    ``assert_no_coverage_contradictions`` sources a clock on a plain assignment
    line, which this permits: it is a documented boundary. What this forbids is
    the FALLBACK shape -- a value that substitutes an invented moment for one the
    caller declined to supply, which is the shape that cannot fail closed because
    it can never tell it was never given one.
    """

    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)

    offenders: list[str] = []
    for node in ast.walk(tree):
        # `a or datetime.now(UTC)`
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(_invents_a_moment(v) for v in node.values):
                offenders.append(f"line {node.lineno}: {ast.unparse(node)}")
        # `datetime.now(UTC) if x is None else x`
        if isinstance(node, ast.IfExp) and (
            _invents_a_moment(node.body) or _invents_a_moment(node.orelse)
        ):
            offenders.append(f"line {node.lineno}: {ast.unparse(node)}")

    assert not offenders, f"{rel} has regained an invented-clock fallback:\n  " + "\n  ".join(
        offenders
    )
