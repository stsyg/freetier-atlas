"""Every pinned block must be GROUNDED in its committed capture (F008 S4 prereq).

This project's cardinal evidence rule is that a claim is **quoted, never
composed**. Until this module existed that rule was carried by two things that
have no failure mode: builder discipline, and a prose sentence in each
``capture.json`` asserting that "every one of the profile's pinned blocks occurs
EXACTLY ONCE in this capture". A sidecar that says so is not a check; a
contributor can paraphrase a sentence into a profile and the sidecar will go on
saying what it always said.

**What this module measures, and why that representation.** ``verbatim`` cannot
mean "appears in the raw HTML bytes". Captures are parsed and normalised --
whitespace collapsed, character references decoded, inline markup flattened --
before the extractor ever compares anything, so a raw-bytes substring scan would
reject correct work whenever a pinned sentence contains an anchor, an ``&nbsp;``
or a line break. The comparison here therefore runs the engine's **own**
``_DocumentCollector`` over the committed bytes and compares against
``normspace(block.text)``: verbatim against exactly the representation the
extractor sees, which is what the runtime path itself compares.

**What this catches that the runtime does not.** ``HtmlDocAdapter`` already
rejects a document whose *required* pinned block is absent -- but only for a
profile that some test actually drives against some document. Three real gaps
survive that, and all three are closed here:

* an ``HtmlTextAssertion`` with ``required=False`` is SILENTLY SKIPPED at
  runtime when it matches nothing (``if not matches: ... continue``), so a
  paraphrased optional pin can never fail;
* a profile registered but never bound to a source in a provider YAML is never
  exercised at all;
* the integration suites that drive providers end to end are
  ``skipif(not DATABASE_URL)``. These are unit tests: they read committed bytes
  and run with no database, so the rule holds even where the integration layer
  is skipped.

Nothing here reaches the network and nothing here writes a file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from app.ingest.adapters._common import normspace
from app.ingest.adapters.html import HTML_EXTRACTION_PROFILES, _DocumentCollector

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_CONFIGS = REPO_ROOT / "config" / "examples" / "providers"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "ingest"


class _DeclaredSource:
    """One ``type: html`` source declared by a provider YAML, plus its capture."""

    def __init__(self, provider: str, source_id: str, profile_name: str, capture: Path) -> None:
        self.provider = provider
        self.source_id = source_id
        self.profile_name = profile_name
        self.capture = capture

    def __repr__(self) -> str:  # pragma: no cover - test id only
        return f"{self.provider}/{self.source_id}"


def _declared_html_sources() -> list[_DeclaredSource]:
    """Every html source declared by every committed provider configuration."""

    found: list[_DeclaredSource] = []
    for config_path in sorted(PROVIDER_CONFIGS.glob("*.yaml")):
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        provider = config["provider"]["id"]
        for source in config.get("sources", []) or []:
            if source.get("type") != "html":
                continue
            capture = FIXTURE_ROOT / provider / "html" / source["id"] / "source.html"
            found.append(
                _DeclaredSource(
                    provider=provider,
                    source_id=source["id"],
                    profile_name=source["extraction_profile"],
                    capture=capture,
                )
            )
    return found


DECLARED_SOURCES = _declared_html_sources()

#: A DECLARED, PRE-EXISTING GAP, recorded rather than papered over.
#:
#: ``config/examples/providers/cloudflare.example.yaml`` declares an official
#: html source ``cloudflare-pages-pricing`` (F005, the Cloudflare provider) whose
#: capture was never committed: ``tests/fixtures/ingest/cloudflare/html/`` holds
#: ``cloudflare-pages-limits`` and ``cloudflare-workers-limits`` only. It is
#: listed here rather than silently skipped so that the gap is visible in code
#: and cannot grow: the test below asserts this set is EXACTLY the set of
#: unbound sources, so a NEW one fails immediately.
#:
#: SCOPE OF THAT PROMISE, measured: of 36 declared sources (34 html, 1 rss,
#: 1 mcp) THREE have no committed capture -- this one, plus
#: ``cloudflare-changelog`` (rss) and ``cloudflare-docs-mcp`` (mcp). The other
#: two are outside this module's html-only reach and are guarded by nothing.
#: All three are Cloudflare and pre-date this slice.
#:
#: It is safe to leave grounded-by-vacuity only because the source resolves to
#: the GENERIC ``pricing_document`` profile, which pins nothing --
#: :func:`test_the_only_unbound_source_pins_nothing` proves that rather than
#: assuming it. A capture for it has to be taken from the live page, which is
#: outside the test suite by design (CI performs zero socket operations), so it
#: is not fixed here.
KNOWN_UNBOUND_SOURCES: frozenset[str] = frozenset({"cloudflare/cloudflare-pages-pricing"})


def _key(source: _DeclaredSource) -> str:
    return f"{source.provider}/{source.source_id}"


def _collect_blocks(capture: Path) -> list[tuple[str, str]]:
    """Return ``(scope, normalised text)`` for every block the extractor would see."""

    collector = _DocumentCollector()
    collector.feed(capture.read_text(encoding="utf-8", errors="replace"))
    collector.close()
    return [(block.scope, normspace(block.text)) for block in collector.text_blocks]


def _pins(source: _DeclaredSource) -> Iterator[tuple[int, object]]:
    profile = HTML_EXTRACTION_PROFILES[source.profile_name]
    yield from enumerate(profile.assertions)


# --- The guard --------------------------------------------------------------


@pytest.mark.parametrize("source", DECLARED_SOURCES, ids=repr)
def test_every_pinned_block_occurs_exactly_once_in_its_committed_capture(
    source: _DeclaredSource,
) -> None:
    """A pinned block is grounded in the capture, or the profile is unsupported.

    PREDICTION, recorded before measurement: all declared sources pass. A pin
    that matches ZERO blocks is a composed or paraphrased quotation; a pin that
    matches MORE THAN ONE is an ``ambiguous_assertion`` waiting to happen the
    first time the source is scanned. Both are refused here.

    ``required=False`` assertions are checked too, deliberately. The runtime
    path skips an unmatched optional pin without a word, so an optional pin is
    the one place a paraphrase could live forever undetected.
    """

    if _key(source) in KNOWN_UNBOUND_SOURCES:
        pytest.skip(f"{_key(source)} has no committed capture; see KNOWN_UNBOUND_SOURCES")

    assert source.capture.exists(), (
        f"{source.provider}/{source.source_id} declares extraction profile "
        f"'{source.profile_name}' but has no committed capture at "
        f"{source.capture.relative_to(REPO_ROOT)}"
    )
    blocks = _collect_blocks(source.capture)
    assert blocks, f"{source.capture.relative_to(REPO_ROOT)} parses to zero text blocks"

    failures: list[str] = []
    for index, assertion in _pins(source):
        matches = sum(
            1 for scope, text in blocks if scope == assertion.scope and text == assertion.text
        )
        if matches != 1:
            anywhere = sum(1 for _, text in blocks if text == assertion.text)
            failures.append(
                f"assertion[{index}] field={assertion.field!r} scope={assertion.scope!r} "
                f"required={assertion.required} matched {matches} block(s) in scope "
                f"({anywhere} in any scope): {assertion.text!r}"
            )

    assert not failures, (
        f"{source.provider}/{source.source_id} (profile '{source.profile_name}') pins text that "
        f"is not grounded exactly once in {source.capture.relative_to(REPO_ROOT)}. Evidence is "
        "quoted, never composed -- fix the profile, never the capture:\n  " + "\n  ".join(failures)
    )


# --- Guards against the guard ----------------------------------------------


def test_the_grounding_walk_is_not_vacuous() -> None:
    """It would look identical if it visited nothing, so count what it visited.

    A parametrised walk that resolves to an empty list still reports success,
    and a walk that found captures but zero pins would too. Both are pinned
    here with hard floors rather than left to inspection.
    """

    assert len(DECLARED_SOURCES) >= 25, (
        f"only {len(DECLARED_SOURCES)} declared html sources found; the walk stopped seeing "
        "the provider configurations"
    )
    pinned = sum(1 for source in DECLARED_SOURCES for _ in _pins(source))
    assert pinned >= 150, f"only {pinned} pinned blocks visited; the walk stopped seeing assertions"

    providers = {source.provider for source in DECLARED_SOURCES}
    assert {"aws", "azure", "gcp", "github", "oracle", "vercel"} <= providers, (
        f"the six merged providers are not all covered; saw {sorted(providers)}"
    )


def test_the_grounding_comparison_discriminates() -> None:
    """The matcher must reject a near-miss, or the walk above proves nothing.

    Takes a real pinned block from a real capture, confirms it IS found, then
    reworded / truncated / re-scoped copies of the SAME block and confirms each
    is NOT. Without this, a comparison that matched everything would pass every
    case above.
    """

    source = next(s for s in DECLARED_SOURCES if s.provider == "github")
    blocks = _collect_blocks(source.capture)
    index, genuine = next(_pins(source))

    found = sum(1 for scope, text in blocks if scope == genuine.scope and text == genuine.text)
    assert found == 1, f"positive control failed: assertion[{index}] matched {found} blocks"

    composed = genuine.text + " Terms apply."
    if " the " in genuine.text:
        paraphrased = genuine.text.replace(" the ", " a ")
    else:
        paraphrased = "x" + genuine.text
    truncated = genuine.text[: max(1, len(genuine.text) // 2)]
    for label, needle in (
        ("composed", composed),
        ("paraphrased", paraphrased),
        ("truncated", truncated),
    ):
        assert needle != genuine.text, f"the {label} mutation did not change the text"
        hits = sum(1 for scope, text in blocks if scope == genuine.scope and text == needle)
        assert hits == 0, f"the {label} needle matched {hits} block(s); the comparison is too loose"

    wrong_scope = "title" if genuine.scope != "title" else "document"
    hits = sum(1 for scope, text in blocks if scope == wrong_scope and text == genuine.text)
    assert hits == 0, "scope is not being honoured; a block matched outside its declared scope"


def test_no_html_source_is_silently_unbound() -> None:
    """Every declared **html** source resolves to a committed capture.

    SCOPE, stated in the name and here so it cannot be discovered later: this
    walk filters to ``type: html`` and therefore inspects **34 of the 36**
    declared sources. The other two are ``rss`` and ``mcp``, which this module
    has no parser for and does not claim to cover.

    MEASURED, and reported rather than absorbed: across every provider config
    there are THREE sources with no committed capture -- ``cloudflare-pages-pricing``
    (html, pinned below), plus ``cloudflare-changelog`` (rss) and
    ``cloudflare-docs-mcp`` (mcp), which are outside this walk's reach and are
    guarded by nothing. All three are Cloudflare (F005), all three pre-date this
    slice, and widening the walk to cover the other two adapters is a separate
    change. The earlier name for this test promised more than its reach
    delivered, which is its own small instance of the defect class this module
    exists for.

    Split out from the main grounding guard on purpose: "the capture is missing"
    and "the capture disagrees with the profile" are different findings, and a
    missing file must not be reported as a grounding failure.

    Equality, not containment. A subset check would let a new unbound html
    source hide behind the allow-list; equality also fails if the known gap is
    fixed, which is the correct moment to delete the entry.
    """

    missing = {_key(s) for s in DECLARED_SOURCES if not s.capture.exists()}
    assert missing == KNOWN_UNBOUND_SOURCES, (
        "the set of declared html sources with no committed capture has changed.\n"
        f"  now unbound:      {sorted(missing)}\n"
        f"  declared as known:{sorted(KNOWN_UNBOUND_SOURCES)}\n"
        "A NEW unbound source means a provider declares a source it cannot prove. "
        "A source that is no longer unbound means the gap was closed -- delete its entry."
    )


def test_the_only_unbound_source_pins_nothing() -> None:
    """The allow-list is safe only because the profile behind it asserts nothing.

    If a source with no committed capture ever pinned a block, the allow-list
    would be exempting exactly the case this module exists to catch. That is
    measured here rather than assumed, so the exemption cannot quietly widen
    into a hole.
    """

    for source in DECLARED_SOURCES:
        if _key(source) not in KNOWN_UNBOUND_SOURCES:
            continue
        profile = HTML_EXTRACTION_PROFILES[source.profile_name]
        assert profile.assertions == (), (
            f"{_key(source)} has no committed capture AND pins "
            f"{len(profile.assertions)} block(s) through profile "
            f"'{source.profile_name}'. The exemption is unsafe: commit its capture."
        )


def test_no_registered_field_name_in_this_repository_is_confusable() -> None:
    """The near-miss threshold's margin, re-derived rather than remembered.

    The threshold in ``app.ingest.vocab`` was chosen because no real field name
    came within edit distance 2 of a reserved name. That was a MEASUREMENT taken
    once, and a measurement written into a comment is prose -- it cannot notice
    when a new provider lands a name that invalidates it.

    So the property is re-derived here, over the WIDEST population: every field
    reachable from the HTML, structured/JSON and MCP registries, with the
    test-support profiles imported as a pytest run imports them. Names, not
    counts: a count-pinning test would go red every time a provider is added,
    which teaches people to edit the number rather than think.

    This test exists because the Level-2 evaluation of the Oracle slice
    (``agent-state/evaluations/F008-p6-oracle-provider-85ef245d.json``) failed on
    a false universal shipped in documentation, refutable only by executing
    across ALL provider modules. Executing across all provider modules is
    precisely what this does.
    """

    from app.ingest.adapters.mcp import MCP_PROFILES
    from app.ingest.adapters.structured import JSON_EXTRACTION_PROFILES
    from app.ingest.vocab import (
        QUOTA_METRIC_NAME,
        RESERVED_FACT_FIELDS,
        confusable_reserved_field,
    )

    import tests.support.html_profiles  # noqa: F401  (registers its profiles)

    names: set[str] = set()
    for profile in HTML_EXTRACTION_PROFILES.values():
        names.update(a.field for a in profile.assertions)
        names.update(c.field for c in profile.columns.values())
        names.update(r.field for r in profile.matrix_rows.values())
        names.update(profile.required_fields)
    for profile in JSON_EXTRACTION_PROFILES.values():
        for spec in getattr(profile, "fields", ()) or ():
            names.add(getattr(spec, "field", ""))
    for profile in MCP_PROFILES.values():
        for attr in ("fields", "columns", "assertions"):
            for item in getattr(profile, attr, ()) or ():
                names.add(getattr(item, "field", "") or "")

    open_names = sorted(n for n in names if n and n not in RESERVED_FACT_FIELDS)

    # Floor: this walk must actually see the corpus, not an empty registry.
    assert len(open_names) >= 150, (
        f"only {len(open_names)} non-reserved field names found; the walk stopped "
        "seeing the profile registries"
    )

    bad_shape = [n for n in open_names if not QUOTA_METRIC_NAME.fullmatch(n)]
    assert not bad_shape, f"registered field names that are not valid metric names: {bad_shape}"

    confusable = [(n, confusable_reserved_field(n)) for n in open_names]
    confusable = [pair for pair in confusable if pair[1] is not None]
    assert not confusable, (
        "a registered field name is confusable with a reserved name, so the near-miss "
        f"threshold no longer has the margin its comment claims: {confusable}"
    )
