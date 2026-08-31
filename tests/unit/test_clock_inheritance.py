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
from datetime import date, timedelta
from pathlib import Path

import pytest
from app.adviser.abuse import admin as abuse_admin
from app.adviser.select import build_candidate, build_pool, gather_candidates
from app.classify.engine import (
    Z0_TRUE_FREE,
    Z2_TEMPORARY_OR_CONDITIONAL,
    OfferFacts,
    classify,
)
from app.classify.orm import classify_offer
from app.ingest.reconcile_coverage import find_coverage_mismatches, reconcile_coverage

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every function converted from an invented clock to an inherited one, with the
#: name of the parameter that must now be supplied.
#:
#: This list is checked for drift by
#: :func:`test_every_clock_bearing_function_in_an_audited_module_is_accounted_for`.
#: It previously held six entries while the contract claimed seven, and the
#: missing one -- ``reconcile_coverage`` -- could be re-optionalised WITHOUT an
#: ``or`` fallback and survive green: the RELOCATOR shape, which forwards
#: ``None`` onward rather than inventing locally, and which the fallback-shape
#: source guard below cannot see.
PARTICIPANTS = [
    pytest.param(classify, "as_of", id="classify"),
    pytest.param(classify_offer, "as_of", id="classify_offer"),
    pytest.param(build_candidate, "as_of", id="build_candidate"),
    pytest.param(build_pool, "as_of", id="build_pool"),
    pytest.param(gather_candidates, "now", id="gather_candidates"),
    pytest.param(find_coverage_mismatches, "now", id="find_coverage_mismatches"),
    pytest.param(reconcile_coverage, "now", id="reconcile_coverage"),
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
    stated narrowly on purpose: for an offer with NO ``available_from``,
    ``as_of`` does NOT change the zero-cost class -- a non-null
    ``available_until`` yields Z2 on either side of the boundary. What it
    changes is the published sentence, and one of the two sentences says a
    closed window is still open.

    **Why that scope clause is now explicit.** This property was measured over a
    population in which ``available_from`` was read by NOTHING, so it was always
    a claim about ``available_until`` alone -- it was merely written as an
    unqualified one. The availability window has since gained an opening gate,
    and for an offer that has not opened yet ``as_of`` DOES move the class. That
    is the gate's purpose, not a regression of the property below.

    So the claim is pinned to its real population: ``available_from=None`` is
    passed EXPLICITLY rather than left to a default, so a later change to
    ``_facts`` cannot silently drift this test out of the population its claim
    covers. The complementary property lives in
    :func:`test_as_of_does_move_the_class_for_an_offer_that_has_not_opened_yet`.

    **The assertions below are unchanged.** Narrowing a claim to what was
    actually measured strengthens it. Deleting or loosening it to accommodate a
    new behaviour would have quietly reopened the defect it was written to
    close, which is why the new behaviour got its own test instead.
    """

    closes = date(2029, 12, 31)
    still_open = classify(
        _facts(available_from=None, available_until=closes), as_of=date(2029, 6, 1)
    )
    expired = classify(_facts(available_from=None, available_until=closes), as_of=date(2030, 6, 1))

    assert still_open.zero_cost_class == expired.zero_cost_class, (
        "as_of is not supposed to move the class; if this fails the defect is "
        "larger than the audit measured and the report must be corrected."
    )
    assert any("bounded availability window" in r for r in still_open.reasons)
    assert any("availability ended" in r for r in expired.reasons)
    assert still_open.reasons != expired.reasons


def test_as_of_does_move_the_class_for_an_offer_that_has_not_opened_yet() -> None:
    """The complementary property the opening gate introduced.

    Deliberately a SEPARATE test rather than an edit to the one above. The two
    cover disjoint populations -- no ``available_from`` versus a non-null one --
    and both must hold. Keeping them apart is what lets the older guard go on
    failing for the case it was written to catch while this one asserts the
    behaviour that was added afterwards.

    A gate that withholds Z0 from an offer that does not exist yet is only half
    the requirement; it must also stop withholding once the offer opens. Both
    directions are asserted here, because a guard that cannot be shown to PERMIT
    is indistinguishable from one that broke the product.
    """

    opens = date(2030, 1, 1)
    facts = _facts(available_from=opens)

    not_yet = classify(facts, as_of=opens - timedelta(days=1))
    now_open = classify(facts, as_of=opens)

    assert not_yet.zero_cost_class != now_open.zero_cost_class, (
        "the opening gate is not consulting as_of at all; a not-yet-open offer "
        "and an open one are being classified identically."
    )
    assert not_yet.zero_cost_class == Z2_TEMPORARY_OR_CONDITIONAL
    assert now_open.zero_cost_class == Z0_TRUE_FREE
    assert any(opens.isoformat() in r for r in not_yet.reasons)


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
    # Added after a Level-2 finding. Its absence was not cosmetic: the headline
    # fix of this slice lives at `_do_publish`, and with publisher.py outside
    # this tuple that fix had NO source-level guard at all.
    "apps/api/app/publish/publisher.py",
)

_INVENTIONS = {"now", "utcnow", "today"}


def _invents_a_moment(node: ast.AST) -> bool:
    """``X.now(...)`` / ``X.utcnow()`` / ``date.today()`` in a defaulting position."""

    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _INVENTIONS
    )


#: Functions that are STILL allowed to source their own moment in an audited
#: module, because this slice deliberately deferred them. Listed by name rather
#: than exempting the whole module, so the exception is visible and cannot grow
#: silently: a third one appearing fails the test below.
#:
#: `publish_candidate` and `publish_scan` invent SEPARATELY inside one
#: `run_provider_scans`, so a single scan run uses two moments. That is a real
#: second finding with its own blast radius (26 test call sites for the paired
#: `reconcile_scan`), and it gets its own slice and its own evidence rather than
#: being tacked onto this one.
_DEFERRED_INVENTORS = {
    "apps/api/app/publish/publisher.py": {"publish_candidate", "publish_scan"},
}

#: Functions in an audited module that REQUIRE a clock but were never conversion
#: participants -- private helpers that were born taking the caller's moment.
#:
#: Measured, not assumed: at the time of writing the audited modules contain
#: eleven functions with a required clock parameter, of which seven are pinned
#: :data:`PARTICIPANTS`; these four are the remainder. They are listed rather
#: than pattern-matched on the leading underscore so that a NEW private helper
#: cannot join them silently -- it fails the closure test until someone decides
#: which bucket it belongs in.
_INTERNAL_CLOCK_REQUIRERS = {
    "apps/api/app/classify/engine.py": {"_availability_reasons"},
    "apps/api/app/publish/publisher.py": {"_build_conditions", "_resolve_offer", "_do_publish"},
}


def _clock_bearing_functions_in(rel: str) -> tuple[set[str], set[str]]:
    """Names in ``rel`` taking a clock, split into (required, optional).

    Read from the source rather than by importing, so a function that is never
    imported by this module is still seen. A parameter counts as REQUIRED when it
    has no default -- positional or keyword-only alike, since a keyword-only
    parameter with no default is every bit as mandatory as a positional one.
    """

    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)

    required: set[str] = set()
    optional: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        positional = args.posonlyargs + args.args
        defaulted = set()
        if args.defaults:
            defaulted = {a.arg for a in positional[len(positional) - len(args.defaults) :]}
        defaulted |= {
            kw.arg
            for kw, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
            if default is not None
        }

        for arg in positional + args.kwonlyargs:
            if arg.arg not in _CLOCK_KWARGS:
                continue
            (optional if arg.arg in defaulted else required).add(node.name)

    return required, optional


def _enclosing_function(tree: ast.AST, lineno: int) -> str:
    best = "<module>"
    best_line = -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if node.lineno > best_line:
                    best, best_line = node.name, node.lineno
    return best


@pytest.mark.parametrize("rel", _AUDITED_MODULES)
def test_an_audited_module_never_defaults_a_clock_it_was_not_given(rel: str) -> None:
    """No ``x = x or datetime.now(...)`` / ``x if x else date.today()`` remains.

    ``assert_no_coverage_contradictions`` sources a clock on a plain assignment
    line, which this permits: it is a documented boundary. What this forbids is
    the FALLBACK shape -- a value that substitutes an invented moment for one the
    caller declined to supply, which is the shape that cannot fail closed because
    it can never tell it was never given one.

    Deferred functions are allowed by NAME via ``_DEFERRED_INVENTORS`` and the
    allowance is checked in both directions, so neither adding a new inventor nor
    fixing a deferred one can leave this test quietly stale.
    """

    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)

    offenders: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        hit = False
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            hit = any(_invents_a_moment(v) for v in node.values)
        if isinstance(node, ast.IfExp):
            hit = _invents_a_moment(node.body) or _invents_a_moment(node.orelse)
        if hit:
            offenders.append(
                (_enclosing_function(tree, node.lineno), f"line {node.lineno}: {ast.unparse(node)}")
            )

    allowed = _DEFERRED_INVENTORS.get(rel, set())
    unexpected = [text for fn, text in offenders if fn not in allowed]
    assert not unexpected, f"{rel} has regained an invented-clock fallback:\n  " + "\n  ".join(
        unexpected
    )

    # The allowance must not outlive the deferral it documents.
    still_inventing = {fn for fn, _ in offenders}
    assert allowed <= still_inventing, (
        f"{rel}: {sorted(allowed - still_inventing)} no longer invent a moment. "
        "Remove them from _DEFERRED_INVENTORS so this guard covers them properly."
    )


# --- WHICH clock, not merely THAT a clock -------------------------------------
# A Level-2 evaluator killed the previous version of this file with one mutation:
#
#     as_of=now.date()  ->  as_of=now.astimezone().date()
#
# Same instant, local calendar date, argument still supplied, no import change.
# That is EXACTLY the base behaviour the slice removed, and it SURVIVED at 2968
# passed, exit 0.
#
# The reason is worth stating because it generalises. The arity mutation (drop
# the argument entirely) is killed by a TypeError, and I had treated that kill as
# proof the fix was guarded. It is not. It proves only that A CLOCK IS PASSED.
# The defect was WHICH CLOCK IS USED. Those are different properties and the
# first does not imply the second.
#
# Nor can a behavioural test close it here: CI runs python:3.13-slim with
# tzname ('UTC','UTC'), where `now.date()` and `now.astimezone().date()` are
# equal by construction. A test that cannot fail in the environment that runs it
# is not a guard. So the assertion has to be STRUCTURAL -- read the source and
# require that the moment handed to a clock parameter is the one the caller was
# given, not a re-derivation of it.

#: Call expressions that RE-DERIVE a moment rather than passing the inherited
#: one. Any of these inside a clock argument means the callee is being handed a
#: different moment from the one its caller is holding.
_REDERIVING_CALLS = {
    "astimezone",  # same instant, DIFFERENT calendar frame -- the L2 mutation
    "localtime",
    "today",
    "now",
    "utcnow",
    "fromtimestamp",
    "mktime",
    "gmtime",
    "combine",
}

#: The keyword names that carry a moment in this codebase.
_CLOCK_KWARGS = {"now", "as_of"}


def _rederivations_in(expr: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(expr):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name in _REDERIVING_CALLS:
            found.append(name)
    return found


@pytest.mark.parametrize("rel", _AUDITED_MODULES)
def test_a_clock_argument_passes_the_inherited_moment_not_a_re_derived_one(rel: str) -> None:
    """Every ``now=``/``as_of=`` argument must be inherited, not re-derived.

    ``as_of=now.date()`` is inherited: it narrows the caller's own moment.
    ``as_of=now.astimezone().date()`` is re-derived: it takes the same instant
    into a different calendar frame, which is the defect wearing the fix's
    clothes. In UTC CI the two are indistinguishable at runtime, so only the
    source can tell them apart.

    Sourcing a clock on its own assignment line is still permitted -- that is how
    a documented boundary like ``assert_no_coverage_contradictions`` works. What
    is forbidden is re-deriving one *inside the argument being passed down*.
    """

    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in _CLOCK_KWARGS:
                continue
            bad = _rederivations_in(kw.value)
            if bad:
                offenders.append(
                    f"line {node.lineno}: {kw.arg}={ast.unparse(kw.value)}  "
                    f"re-derives via {sorted(set(bad))}"
                )

    assert not offenders, (
        f"{rel} hands a RE-DERIVED moment to a clock parameter:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe callee must receive the moment its caller is holding. Passing "
        "the same instant in a different frame is the defect this slice removed."
    )


def test_every_clock_bearing_function_in_an_audited_module_is_accounted_for() -> None:
    """The pinned list must not silently drift from what the SOURCE contains.

    A Level-2 evaluator found the contract claiming SEVEN converted participants
    while this file pinned SIX. The unpinned one, ``reconcile_coverage``, could
    be re-optionalised without an ``or`` fallback and survive green -- the
    RELOCATOR shape, which forwards ``None`` onward instead of inventing locally
    and which the fallback-shape guard above cannot see.

    A list maintained by hand drifts. This makes the drift fail a test instead.

    **Why this reads the source and NOT ``agent-state/current_contract.json``.**
    It used to read that file and compare against
    ``classification.PARTICIPANT_converted``. That was a real coupling with a
    real cost: ``current_contract.json`` describes the slice CURRENTLY IN
    FLIGHT and every builder is instructed to overwrite it wholesale, so a
    permanent guard was reading a single-slot scratchpad. Two consecutive
    builders had to notice the trap while planning and carry the block forward
    verbatim; the third would eventually not, and the failure would surface as a
    unit-test error with no code change to explain it. The same hazard has
    ALREADY landed elsewhere: ``apps/api/app/ingest/config_sync.py`` still cites
    "AMENDMENT 8 in ``agent-state/current_contract.json``", a section that no
    longer exists in that file.

    The remedy is not to manage the coupling but to remove it, and the honest
    replacement is not a second hand-written list next to the first -- two lists
    in one file are edited in one breath, which would leave the guard weaker
    than the cross-artifact check it replaced. The durable oracle is THE SOURCE
    ITSELF: derive every clock-bearing function in the audited modules and
    require that each one is classified into exactly one bucket here.

    That closes the original defect from both sides:

    * drop a participant from :data:`PARTICIPANTS` and it appears in the derived
      REQUIRED set with no bucket, so this fails;
    * re-optionalise it as well -- the RELOCATOR move that survives every other
      guard in this file -- and it appears in the derived OPTIONAL set without
      being a documented deferral, so this still fails.

    Deliberately NOT reintroduced: a rule of the form "overwrite the contract
    file except for the parts you must not". That is precisely the invisible
    coupling that caused the incident, and enforcing it would need a second
    permanent test reading the same volatile file.
    """

    unclassified_required: list[str] = []
    unclassified_optional: list[str] = []
    pinned_missing_from_source: list[str] = []
    stale_internal: list[str] = []

    participants_by_module: dict[str, set[str]] = {}
    for param in PARTICIPANTS:
        func = param.values[0]
        rel = Path(inspect.getsourcefile(func)).resolve().relative_to(REPO_ROOT).as_posix()
        participants_by_module.setdefault(rel, set()).add(func.__name__)

    # Found by mutation, not by reasoning: pinning a participant that lives
    # OUTSIDE _AUDITED_MODULES made it invisible to the loop below, because the
    # loop only ever visits audited modules. A participant with no source-level
    # audit is half-guarded, so the two lists are required to agree on scope
    # before anything else is checked.
    unaudited = sorted(set(participants_by_module) - set(_AUDITED_MODULES))
    assert not unaudited, (
        "PARTICIPANTS pins a function from a module that is not audited:\n  "
        + "\n  ".join(unaudited)
        + "\n\nAdd the module to _AUDITED_MODULES so the source-level guards "
        "cover it, or the pin buys only a signature check."
    )

    for rel in _AUDITED_MODULES:
        required, optional = _clock_bearing_functions_in(rel)

        pinned = participants_by_module.get(rel, set())
        internal = _INTERNAL_CLOCK_REQUIRERS.get(rel, set())
        deferred = _DEFERRED_INVENTORS.get(rel, set())

        unclassified_required += [f"{rel}::{n}" for n in sorted(required - pinned - internal)]
        unclassified_optional += [f"{rel}::{n}" for n in sorted(optional - deferred)]
        pinned_missing_from_source += [f"{rel}::{n}" for n in sorted(pinned - required)]
        stale_internal += [f"{rel}::{n}" for n in sorted(internal - required)]

    # A participant pinned here but no longer REQUIRING a clock in source is the
    # exact regression this file exists to catch, so it is named first.
    assert not pinned_missing_from_source, (
        "PARTICIPANTS pins a function that no longer requires a clock in source:\n  "
        + "\n  ".join(pinned_missing_from_source)
        + "\n\nEither the conversion was undone, or the function moved out of "
        "_AUDITED_MODULES and lost its source-level guard."
    )
    assert not unclassified_required, (
        "an audited module REQUIRES a clock in a function that is classified nowhere:\n  "
        + "\n  ".join(unclassified_required)
        + "\n\nPin it in PARTICIPANTS if it was converted, or list it in "
        "_INTERNAL_CLOCK_REQUIRERS if it always required one. Leaving it "
        "unclassified is how a converted function silently stops being guarded."
    )
    assert not unclassified_optional, (
        "an audited module makes a clock OPTIONAL in a function that is not a "
        "documented deferral:\n  "
        + "\n  ".join(unclassified_optional)
        + "\n\nAn optional clock lets a caller silently receive a SECOND moment. "
        "Convert it, or add it to _DEFERRED_INVENTORS with the reason."
    )
    # Mirrors the both-directions rule on _DEFERRED_INVENTORS: an allowance must
    # not outlive the thing it documents, or the bucket rots into a blanket
    # exemption nobody rechecks.
    assert not stale_internal, (
        "_INTERNAL_CLOCK_REQUIRERS names a function that no longer requires a clock:\n  "
        + "\n  ".join(stale_internal)
        + "\n\nRemove it so this guard keeps covering what it claims to cover."
    )
