"""The evidence floor, swept across EVERY source adapter discovered on disk.

WHY THIS FILE EXISTS
--------------------
The evidence floor is the guard that refuses a candidate carrying no evidence
location. On a product whose first rule is that no unsupported claim that a
service is free may ever ship, it is the single most load-bearing line in the
ingest path -- and it is implemented **five separate times**, once per adapter.

Measured on this tree (2026-08-25) with two independent line tracers
(``sys.settrace`` and ``sys.monitoring``) over the WHOLE suite, run both with a
live PostgreSQL and without one, with a traced-line floor and nine positive
controls to prove the instruments were attached:

* four of the five guards had **never been executed by any test**, with or
  without a database;
* the fifth (``app.ingest.reference``) *was* executed -- but only incidentally,
  by a test that asserts the two missing-field messages and ignores the floor
  message entirely. It was covered and still unasserted, which is worse: a
  coverage report called it green while deleting the guard would have broken
  no test.

So a regression in any one of the five was silent. That is what this file ends.

WHY ONE PARAMETERISED TEST AND NOT FIVE
---------------------------------------
The defect is *drift between copies*. Five separate tests would recreate the
exact structure that let the property rot unevenly in the first place. There is
one test; it is parameterised over a list that is **discovered by walking the
package**, never hand-written, and a companion floor fails the moment an adapter
exists on disk that this sweep does not cover. Adding a sixth adapter without
its evidence floor is therefore impossible, not merely unlikely.

Each case carries a PAIRED POSITIVE CONTROL. A test that asserts "rejected"
passes just as well against code that rejects everything, so the accepting arm
is asserted too: the identical candidate WITH evidence must be accepted with no
problems at all, and the only difference between the two arms must be the floor
message itself.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from app.ingest.adapters.html import HtmlDocAdapter, resolve_profile
from app.ingest.adapters.mcp import MCP_PROFILES, McpToolAdapter, OfflineMcpClient
from app.ingest.adapters.rss import RssFeedAdapter
from app.ingest.adapters.structured import JSON_EXTRACTION_PROFILES, StructuredApiAdapter
from app.ingest.base import CandidateFacts, EvidenceLocation, SourceAdapter
from app.ingest.fetch import FetchPolicy, OfflineFetcher
from app.ingest.reference import JsonOfferAdapter

#: The one message every adapter must produce. Asserting the identical literal
#: in every case is what turns five copies back into one property: a reworded
#: copy fails here rather than drifting quietly.
EVIDENCE_FLOOR_MESSAGE = "Candidate has no evidence location."

_SOURCE_URL = "https://provider.example/free"
_POLICY = FetchPolicy(official_domains=("provider.example",))

#: Facts satisfying the required-field check of every adapter, so that the ONLY
#: problem a well-formed candidate can raise is the evidence floor. ``link`` is
#: required by the RSS adapter specifically.
_COMPLETE_FACTS = {
    "service": "Widgets",
    "offer_type": "always_free",
    "link": _SOURCE_URL,
}

_TESTS_ROOT = Path(__file__).resolve().parents[1]
_API_ROOT = _TESTS_ROOT.parent / "apps" / "api"
_INGEST_ROOT = _API_ROOT / "app" / "ingest"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_API_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _discover_concrete_adapters() -> tuple[dict[type[SourceAdapter], str], int]:
    """Import every module under ``app.ingest`` and collect concrete adapters.

    Discovery, not a hand-written list: the point of the sweep is that a new
    adapter cannot escape it, and a list typed into this file would be exactly
    the thing that goes stale. ``obj.__module__ == module.__name__`` keeps a
    re-export (``app.ingest.__init__`` re-exports several adapters) from being
    counted as a second, distinct adapter.
    """

    found: dict[type[SourceAdapter], str] = {}
    modules_walked = 0
    for path in sorted(_INGEST_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        modules_walked += 1
        module = importlib.import_module(_module_name(path))
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, SourceAdapter)
                and obj is not SourceAdapter
                and not inspect.isabstract(obj)
                and obj.__module__ == module.__name__
            ):
                found[obj] = f"{obj.__module__}.{name}"
    return found, modules_walked


DISCOVERED_ADAPTERS, MODULES_WALKED = _discover_concrete_adapters()


# --- how to build one instance of each adapter ------------------------------
# Only ``validate()`` is exercised, so each builder supplies the minimum an
# adapter needs to answer it. The map is keyed by the imported CLASS rather than
# by a string, so a typo here is an ImportError rather than a silent gap.


def _build_html() -> HtmlDocAdapter:
    return HtmlDocAdapter(
        OfflineFetcher(_POLICY), (_SOURCE_URL,), resolve_profile("quota_document")
    )


def _build_structured() -> StructuredApiAdapter:
    return StructuredApiAdapter(
        OfflineFetcher(_POLICY), (_SOURCE_URL,), JSON_EXTRACTION_PROFILES["offer_api"]
    )


def _build_mcp() -> McpToolAdapter:
    return McpToolAdapter(
        OfflineFetcher(_POLICY),
        client=OfflineMcpClient(),
        source_urls=(_SOURCE_URL,),
        profile=MCP_PROFILES["mcp_offer_catalogue"],
    )


def _build_rss() -> RssFeedAdapter:
    return RssFeedAdapter(OfflineFetcher(_POLICY), (_SOURCE_URL,))


def _build_reference() -> JsonOfferAdapter:
    return JsonOfferAdapter(OfflineFetcher(_POLICY), (_SOURCE_URL,))


_BUILDERS: dict[type[SourceAdapter], Callable[[], SourceAdapter]] = {
    HtmlDocAdapter: _build_html,
    StructuredApiAdapter: _build_structured,
    McpToolAdapter: _build_mcp,
    RssFeedAdapter: _build_rss,
    JsonOfferAdapter: _build_reference,
}


def _label(adapter_cls: type[SourceAdapter]) -> str:
    return DISCOVERED_ADAPTERS.get(adapter_cls, adapter_cls.__qualname__)


# --- the floor that makes escape impossible ---------------------------------


def test_the_sweep_covers_every_adapter_that_exists_on_disk() -> None:
    """A new adapter cannot be added without wiring its evidence floor.

    This is the guard on the guard. Without it the parameterised sweep below
    would keep passing while a sixth adapter shipped an unswept -- or absent --
    evidence floor, which is precisely how four of the five existing copies came
    to be untested.
    """

    # Vacuity floors first: a walk that found nothing must fail loudly rather
    # than report a confident all-clear over an empty set.
    assert MODULES_WALKED >= 15, (
        f"only {MODULES_WALKED} modules walked under {_INGEST_ROOT}; the package walk "
        "stopped seeing the ingest package, so this sweep is vacuous"
    )
    assert len(DISCOVERED_ADAPTERS) >= 5, (
        f"only {len(DISCOVERED_ADAPTERS)} concrete SourceAdapter subclasses discovered; "
        "the discovery is broken, so an empty sweep would pass by accident"
    )

    discovered = set(DISCOVERED_ADAPTERS)
    covered = set(_BUILDERS)
    unswept = sorted(_label(cls) for cls in discovered - covered)
    stale = sorted(_label(cls) for cls in covered - discovered)
    assert not unswept, (
        "these adapters exist on disk but this evidence-floor sweep does not cover them, "
        "so a missing or reworded evidence floor in them would be silent:\n  "
        + "\n  ".join(unswept)
        + "\nAdd a builder to _BUILDERS for each."
    )
    assert not stale, (
        "these adapters are swept here but no longer exist on disk:\n  " + "\n  ".join(stale)
    )


@pytest.mark.parametrize(
    "adapter_cls",
    sorted(DISCOVERED_ADAPTERS, key=lambda cls: DISCOVERED_ADAPTERS[cls]),
    ids=lambda cls: DISCOVERED_ADAPTERS[cls],
)
def test_every_adapter_refuses_a_candidate_with_no_evidence(
    adapter_cls: type[SourceAdapter],
) -> None:
    """A candidate carrying no evidence location is refused -- by all of them.

    Both arms are asserted, because a one-armed test cannot distinguish a
    working guard from code that rejects everything:

    * NEGATIVE arm  - identical facts, ``evidence=()`` -> the floor message, and
      *only* the floor message.
    * POSITIVE arm  - identical facts, one ``EvidenceLocation`` -> accepted with
      no problems at all.

    The two arms differ in exactly one input, so the delta between their problem
    lists is the guard's entire observable effect.
    """

    builder = _BUILDERS.get(adapter_cls)
    assert builder is not None, (
        f"{_label(adapter_cls)} was discovered on disk but has no builder here; "
        "test_the_sweep_covers_every_adapter_that_exists_on_disk explains the fix"
    )
    adapter = builder()

    without_evidence = CandidateFacts(
        provider="provider.example",
        source_url=_SOURCE_URL,
        facts=dict(_COMPLETE_FACTS),
    )
    with_evidence = CandidateFacts(
        provider="provider.example",
        source_url=_SOURCE_URL,
        facts=dict(_COMPLETE_FACTS),
        evidence=(EvidenceLocation(url=_SOURCE_URL, selector="$.offers[0]"),),
    )
    # The arms must differ in evidence and in nothing else.
    assert without_evidence.facts == with_evidence.facts
    assert without_evidence.evidence == ()
    assert len(with_evidence.evidence) == 1

    problems_without = list(adapter.validate(without_evidence))
    problems_with = list(adapter.validate(with_evidence))

    assert problems_without == [EVIDENCE_FLOOR_MESSAGE], (
        f"{_label(adapter_cls)} did not refuse an evidence-free candidate with the shared "
        f"message; got {problems_without!r}"
    )
    assert problems_with == [], (
        f"{_label(adapter_cls)} rejected a well-formed candidate that HAS evidence, so the "
        f"negative arm above proves nothing; got {problems_with!r}"
    )
    assert set(problems_without) - set(problems_with) == {EVIDENCE_FLOOR_MESSAGE}


def test_all_adapters_agree_on_the_wording_of_the_floor() -> None:
    """Five implementations, one message -- checked as one set, not five strings.

    The message reaches operators and the review queue. If one copy drifts the
    others still pass their own case, so drift is only visible when the wordings
    are compared against each other.
    """

    messages: dict[str, str] = {}
    for adapter_cls, label in DISCOVERED_ADAPTERS.items():
        builder = _BUILDERS[adapter_cls]
        problems = list(
            builder().validate(
                CandidateFacts(
                    provider="provider.example",
                    source_url=_SOURCE_URL,
                    facts=dict(_COMPLETE_FACTS),
                )
            )
        )
        assert len(problems) == 1, f"{label} reported {problems!r}, expected exactly one problem"
        messages[label] = problems[0]

    assert len(messages) >= 5, "fewer adapters answered than were discovered"
    assert set(messages.values()) == {EVIDENCE_FLOOR_MESSAGE}, (
        "the adapters no longer agree on the evidence-floor message:\n  "
        + "\n  ".join(f"{label}: {message!r}" for label, message in sorted(messages.items()))
    )
