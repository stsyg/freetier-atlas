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

**TWO POPULATIONS, and why they differ.** This module walks declared sources
twice, over deliberately different scopes:

* :data:`DECLARED_SOURCES` -- **every** source of **every** type. Used by the
  unbound-source guard, which only asks "does a committed capture exist?" and
  needs no parser to answer.
* :data:`HTML_SOURCES` -- the ``type: html`` subset. Used by the grounding
  comparison, which must run the HTML collector over the bytes and therefore
  genuinely cannot cover ``rss`` or ``mcp``.

The split is the point. Until this slice BOTH walks were html-scoped, so
``test_no_html_source_is_silently_unbound`` inspected 34 of 36 declared sources
and caught 1 of the 3 sources that have no capture. A guard whose reach is
narrower than the property its name implies is the exact defect class this
module exists to catch, and it had grown one of its own. The reach is now the
whole population; where a walk still *must* be narrow (the parser-bound one) the
narrowing is expressed as a separate, explicitly-named population rather than as
a ``continue`` buried in a loop.

Nothing here reaches the network and nothing here writes a file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from app.ingest.adapters._common import normspace
from app.ingest.adapters.html import HTML_EXTRACTION_PROFILES, _DocumentCollector

from tests.support.fixtures import _ADAPTER_EXTENSION, _ADAPTER_SOURCE_TYPE

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_CONFIGS = REPO_ROOT / "config" / "examples" / "providers"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "ingest"

#: Source ``type`` -> fixture adapter directory, INVERTED FROM THE REPOSITORY'S
#: OWN MAP rather than restated here.
#:
#: ``tests/support/fixtures.py`` already declares how an adapter directory maps
#: to a source type and to a capture file extension, and the production runner
#: (``app.ingest.runner``) resolves the same ``source.html|xml|json`` names. A
#: second hand-written copy of that convention in this module would be a
#: snapshot of the day it was typed: it would keep passing while drifting away
#: from the loader it claims to mirror. Deriving it means a new adapter is
#: picked up here the moment the harness learns about it.
_ADAPTER_FOR_SOURCE_TYPE: dict[str, str] = {
    source_type: adapter for adapter, source_type in _ADAPTER_SOURCE_TYPE.items()
}


class _DeclaredSource:
    """One source declared by a provider YAML, plus where its capture would live.

    ``profile_name`` is ``None`` for source types that carry no
    ``extraction_profile`` key at all (``rss``, ``mcp``): those are bound to an
    adapter by their type, not by a named profile.
    """

    def __init__(
        self,
        provider: str,
        source_id: str,
        source_type: str,
        profile_name: str | None,
        capture: Path | None,
    ) -> None:
        self.provider = provider
        self.source_id = source_id
        self.source_type = source_type
        self.profile_name = profile_name
        self.capture = capture

    @property
    def has_capture(self) -> bool:
        """True when a committed capture document exists for this source.

        An unmapped source type has ``capture is None``; it is reported by
        :func:`test_every_declared_source_type_has_a_known_capture_convention`
        rather than being quietly counted as bound here.
        """

        return self.capture is not None and self.capture.is_file()

    def __repr__(self) -> str:  # pragma: no cover - test id only
        return f"{self.provider}/{self.source_id}"


def _capture_path(provider: str, source_type: str, source_id: str) -> Path | None:
    """Where a capture for this source would live, or ``None`` if unmappable."""

    adapter = _ADAPTER_FOR_SOURCE_TYPE.get(source_type)
    if adapter is None:
        return None
    extension = _ADAPTER_EXTENSION.get(adapter)
    if extension is None:  # pragma: no cover - the two maps are keyed alike
        return None
    return FIXTURE_ROOT / provider / adapter / source_id / f"source.{extension}"


def _declared_sources() -> list[_DeclaredSource]:
    """EVERY source of EVERY type declared by every committed provider config.

    Deliberately unfiltered. The html-only view is taken separately, below, by
    the walks that genuinely need an HTML parser -- so that narrowing is a named
    population a reader can see, not a ``continue`` inside this loop.
    """

    found: list[_DeclaredSource] = []
    for config_path in sorted(PROVIDER_CONFIGS.glob("*.yaml")):
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        provider = config["provider"]["id"]
        for source in config.get("sources", []) or []:
            source_type = str(source.get("type"))
            found.append(
                _DeclaredSource(
                    provider=provider,
                    source_id=source["id"],
                    source_type=source_type,
                    profile_name=source.get("extraction_profile"),
                    capture=_capture_path(provider, source_type, source["id"]),
                )
            )
    return found


#: Every declared source, of every type. The unbound-source guard's population.
DECLARED_SOURCES = _declared_sources()

#: The ``type: html`` subset. The grounding comparison's population -- narrower
#: because it must run the HTML collector over the committed bytes.
HTML_SOURCES = [s for s in DECLARED_SOURCES if s.source_type == "html"]

#: DECLARED, PRE-EXISTING GAPS, recorded rather than papered over.
#:
#: Three sources declared by ``config/examples/providers/cloudflare.example.yaml``
#: (F005, the Cloudflare provider) have no committed capture. All three pre-date
#: this slice and all three are Cloudflare:
#:
#: * ``cloudflare-pages-pricing`` (html) -- resolves to the GENERIC
#:   ``pricing_document`` profile;
#: * ``cloudflare-changelog``    (rss)  -- declares no extraction profile;
#: * ``cloudflare-docs-mcp``     (mcp)  -- declares no extraction profile.
#:
#: MEASURED, not quoted: re-derived from the configs by the walk above, over the
#: population ``config/examples/providers/*.yaml`` (7 files, 36 sources: 34 html,
#: 1 rss, 1 mcp). Until this slice only the html one was pinned, because the walk
#: that checked the set was itself html-only -- it caught 1 of the 3.
#:
#: Listing them here rather than skipping them silently is what makes the gap
#: visible in code and stops it growing: the guard below asserts this set is
#: EXACTLY the set of unbound sources, so a NEW one fails immediately. That set
#: equality is the ONLY reason pinning a known gap is legitimate. It is not an
#: allow-list to be widened -- if a new entry is needed, that is the finding.
#:
#: These are safe to leave grounded-by-vacuity only because none of the three
#: pins any text; :func:`test_every_unbound_source_pins_nothing` proves that
#: rather than assuming it. Committing captures for them means fetching live
#: pages, which is outside the test suite by design (CI performs zero socket
#: operations), so it is not done here.
KNOWN_UNBOUND_SOURCES: frozenset[str] = frozenset(
    {
        "cloudflare/cloudflare-pages-pricing",
        "cloudflare/cloudflare-changelog",
        "cloudflare/cloudflare-docs-mcp",
    }
)


def _key(source: _DeclaredSource) -> str:
    return f"{source.provider}/{source.source_id}"


def _collect_blocks(capture: Path) -> list[tuple[str, str]]:
    """Return ``(scope, normalised text)`` for every block the extractor would see."""

    collector = _DocumentCollector()
    collector.feed(capture.read_text(encoding="utf-8", errors="replace"))
    collector.close()
    return [(block.scope, normspace(block.text)) for block in collector.text_blocks]


def _pins(source: _DeclaredSource) -> Iterator[tuple[int, object]]:
    if source.profile_name is None:
        return
    profile = HTML_EXTRACTION_PROFILES[source.profile_name]
    yield from enumerate(profile.assertions)


# --- The guard --------------------------------------------------------------


@pytest.mark.parametrize("source", HTML_SOURCES, ids=repr)
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

    assert source.capture is not None, (
        f"{_key(source)} (type={source.source_type!r}) has no known capture convention"
    )
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

    BOTH populations are floored. The all-types walk is the one that regressed
    into an html-only walk once already; a floor on the html subset alone would
    have stayed green throughout that regression, which is precisely why the
    relationship between the two is asserted here and not just their sizes.
    """

    assert len(HTML_SOURCES) >= 25, (
        f"only {len(HTML_SOURCES)} declared html sources found; the walk stopped seeing "
        "the provider configurations"
    )
    pinned = sum(1 for source in HTML_SOURCES for _ in _pins(source))
    assert pinned >= 150, f"only {pinned} pinned blocks visited; the walk stopped seeing assertions"

    providers = {source.provider for source in HTML_SOURCES}
    assert {"aws", "azure", "gcp", "github", "oracle", "vercel"} <= providers, (
        f"the six merged providers are not all covered; saw {sorted(providers)}"
    )

    # The all-types walk must be STRICTLY WIDER than the html one, or it has
    # silently collapsed back into an html-only walk. Measured over
    # config/examples/providers/*.yaml: 36 sources, of which 34 are html.
    assert len(DECLARED_SOURCES) > len(HTML_SOURCES), (
        f"the all-types walk ({len(DECLARED_SOURCES)}) is no wider than the html-only walk "
        f"({len(HTML_SOURCES)}). Either every non-html source was deleted from the provider "
        "configs, or a type filter has crept back into _declared_sources()."
    )
    non_html = {s.source_type for s in DECLARED_SOURCES} - {"html"}
    assert non_html, "no non-html source types are being walked at all"


def test_every_declared_source_type_has_a_known_capture_convention() -> None:
    """A source type this module cannot place must FAIL, never be skipped.

    The widened walk resolves a capture path by inverting the harness's own
    adapter maps. If a provider ever declares a type those maps do not know,
    ``_capture_path`` returns ``None`` -- and an unmappable source would then be
    neither confirmed bound nor reported unbound. That is the silent-skip
    failure mode this whole module exists to refuse, so it is made loud here
    instead of being absorbed by the guard below.
    """

    unmappable = sorted(
        f"{s.provider}/{s.source_id} (type={s.source_type!r})"
        for s in DECLARED_SOURCES
        if s.capture is None
    )
    assert not unmappable, (
        "a provider declares a source type with no known capture convention, so the "
        "unbound-source guard cannot see it either way:\n  " + "\n  ".join(unmappable) + "\n"
        f"Known source types: {sorted(_ADAPTER_FOR_SOURCE_TYPE)}. Teach "
        "tests/support/fixtures.py about the new adapter rather than special-casing it here."
    )


def test_the_grounding_comparison_discriminates() -> None:
    """The matcher must reject a near-miss, or the walk above proves nothing.

    Takes a real pinned block from a real capture, confirms it IS found, then
    reworded / truncated / re-scoped copies of the SAME block and confirms each
    is NOT. Without this, a comparison that matched everything would pass every
    case above.
    """

    source = next(s for s in HTML_SOURCES if s.provider == "github")
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


def test_no_declared_source_of_any_type_is_silently_unbound() -> None:
    """Every declared source -- of EVERY type -- resolves to a committed capture.

    SCOPE, and it is now the whole population: this walk covers every source in
    every ``config/examples/providers/*.yaml``, not just ``type: html``.
    MEASURED at the time of writing: 7 provider configs, 36 declared sources
    (34 html, 1 rss, 1 mcp), of which THREE have no committed capture -- all
    three Cloudflare, all three pre-dating this slice, all three now pinned in
    :data:`KNOWN_UNBOUND_SOURCES`.

    WHY THE WIDENING, rather than a more honest name. The previous version of
    this test filtered to ``type: html`` and so inspected 34 of 36 sources: it
    caught ``cloudflare-pages-pricing`` and was structurally blind to
    ``cloudflare-changelog`` (rss) and ``cloudflare-docs-mcp`` (mcp), which were
    guarded by nothing at all. Renaming it to admit that would have preserved
    the gap while making it honest. A guard scoped narrower than the property it
    is trusted for IS the failure, so the reach was widened to match instead.
    Answering "does a capture exist?" needs no parser, so there was never a
    technical reason for the narrow scope -- only the accident of which walk it
    was written next to.

    Split out from the main grounding guard on purpose: "the capture is missing"
    and "the capture disagrees with the profile" are different findings, and a
    missing file must not be reported as a grounding failure.

    Equality, not containment. A subset check would let a new unbound source
    hide behind the pinned set; equality also fails if a known gap is closed,
    which is the correct moment to delete that entry. Do NOT relax this to a
    subset, a prefix or a pattern to make a new source pass -- the whole reason
    pinning three known gaps is defensible is that any FOURTH one fails loudly.
    """

    missing = {_key(s) for s in DECLARED_SOURCES if not s.has_capture}
    assert missing == KNOWN_UNBOUND_SOURCES, (
        "the set of declared sources with no committed capture has changed.\n"
        f"  now unbound:       {sorted(missing)}\n"
        f"  declared as known: {sorted(KNOWN_UNBOUND_SOURCES)}\n"
        f"  (walked {len(DECLARED_SOURCES)} sources of types "
        f"{sorted({s.source_type for s in DECLARED_SOURCES})})\n"
        "A NEW unbound source means a provider declares a source it cannot prove. "
        "A source that is no longer unbound means the gap was closed -- delete its entry."
    )


def test_the_unbound_guard_refuses_and_permits() -> None:
    """Two-sided control: the guard must be able to FAIL and to PASS.

    An instrument that cannot pass is as useless as one that cannot fail. The
    equality above is executed against real repository state, so on a green run
    it is indistinguishable from an assertion that always holds. Both directions
    are therefore exercised here against the same comparison.

    REFUSE arm: an extra unbound source is not absorbed.
    PERMIT arm: a source that HAS its capture is not flagged, and the set of
    known gaps closing is recognised as the gaps closing.
    """

    real = {_key(s) for s in DECLARED_SOURCES if not s.has_capture}

    # REFUSE: one more unbound source than declared must break the equality.
    intruder = real | {"acme/acme-undocumented-pricing"}
    assert intruder != KNOWN_UNBOUND_SOURCES, (
        "adding an unbound source did not change the comparison; the guard cannot fail"
    )

    # REFUSE: dropping a pinned gap must also break it (equality, not subset).
    if real:
        assert (real - {sorted(real)[0]}) != KNOWN_UNBOUND_SOURCES, (
            "removing a known gap did not change the comparison; the guard is a subset check"
        )

    # PERMIT: every source NOT in the known set really does have its bytes on
    # disk, so the guard is passing because the repository is sound -- not
    # because `has_capture` is stuck returning True.
    bound = [s for s in DECLARED_SOURCES if _key(s) not in KNOWN_UNBOUND_SOURCES]
    assert bound, "positive control is vacuous: no bound sources to check"
    for source in bound:
        assert source.has_capture, f"{_key(source)} is bound but its capture is missing"
        assert source.capture is not None and source.capture.stat().st_size > 0, (
            f"{_key(source)} has a capture file that is empty"
        )

    # PERMIT: and `has_capture` is genuinely capable of returning False, or the
    # loop above proves only that it is a constant.
    assert not any(s.has_capture for s in DECLARED_SOURCES if _key(s) in KNOWN_UNBOUND_SOURCES), (
        "a source pinned as unbound now has a capture; has_capture may be stuck True"
    )


def test_every_unbound_source_pins_nothing() -> None:
    """The pinned gaps are safe only because nothing behind them asserts text.

    If a source with no committed capture ever pinned a block, the pinned set
    would be exempting exactly the case this module exists to catch. That is
    measured here rather than assumed, so the exemption cannot quietly widen
    into a hole.

    Covers all three known gaps, of all three types. For the html one the
    profile registry is consulted directly; for ``rss``/``mcp`` the property is
    that the source declares no extraction profile at all, which is asserted
    rather than inferred from the absence of a lookup.
    """

    checked = 0
    for source in DECLARED_SOURCES:
        if _key(source) not in KNOWN_UNBOUND_SOURCES:
            continue
        checked += 1
        if source.profile_name is None:
            continue
        profile = HTML_EXTRACTION_PROFILES[source.profile_name]
        assert profile.assertions == (), (
            f"{_key(source)} has no committed capture AND pins "
            f"{len(profile.assertions)} block(s) through profile "
            f"'{source.profile_name}'. The exemption is unsafe: commit its capture."
        )

    assert checked == len(KNOWN_UNBOUND_SOURCES), (
        f"only {checked} of {len(KNOWN_UNBOUND_SOURCES)} pinned sources were found in the "
        "declared population. A pinned entry that matches no declared source is dead weight "
        "hiding behind a name -- delete it, or fix the key."
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
