"""Provenance-sidecar integrity for committed fixtures (F008 S3, decision Q2-A).

Every fixture captured from a **real provider** carries a ``capture.json``
sidecar recording where the bytes came from. This module asserts that the
sidecar is *present*, *complete* and *consistent with the bytes on disk*.

It deliberately asserts **nothing about freshness**. A "the fixture must be
newer than N days" check in CI is a time bomb: it reddens the build on a
calendar boundary rather than on a real defect, and it makes an offline suite
depend on the wall clock. Freshness is a **runtime** concern, and the pipeline
already enforces it where it belongs -- ``assess_staleness`` flags a stale source
and the publication gate refuses to publish it (see
``tests/integration/test_ingest_stale.py``).
"""

from __future__ import annotations

import ast
import hashlib
import json
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "ingest"
TESTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The ``example`` corpus is synthetic -- hand-written to exercise adapter shapes
#: rather than captured from anyone. It represents no real source, so it has no
#: provenance to record and is exempt from the sidecar requirement.
SYNTHETIC_PROVIDERS = frozenset({"example"})

REQUIRED_FIELDS = (
    "url",
    "fetched_at",
    "http_status",
    "sha256_original",
    "sha256_stored",
    "trim_method",
    "robots_allowed",
    "tos_note",
    "captured_by",
)


def _fixture_dirs(*, real_providers: bool) -> list[Path]:
    """Every fixture case directory, split by synthetic vs real provider."""

    found = []
    for expected in sorted(FIXTURE_ROOT.glob("*/*/*/expected.json")):
        provider = expected.relative_to(FIXTURE_ROOT).parts[0]
        is_real = provider not in SYNTHETIC_PROVIDERS
        if is_real == real_providers:
            found.append(expected.parent)
    return found


def _source_file(directory: Path) -> Path:
    sources = sorted(p for p in directory.glob("source.*"))
    assert len(sources) == 1, f"{directory}: expected exactly one source.<ext>, got {sources}"
    return sources[0]


def _relative(path: Path) -> str:
    return path.relative_to(FIXTURE_ROOT).as_posix()


REAL_PROVIDER_DIRS = _fixture_dirs(real_providers=True)

#: Every committed provenance sidecar, derived from the tree rather than a
#: hand-written list so a new capture is covered the moment it lands. Only real
#: providers carry a ``capture.json`` (the synthetic corpus is exempt), so this
#: glob is the whole population the disclosure guard must reason about.
ALL_CAPTURE_SIDECARS = sorted(FIXTURE_ROOT.glob("**/capture.json"))

#: A floor on the corpus size. It exists purely so a glob that silently matches
#: nothing (a moved fixture root, a renamed file) cannot make the disclosure
#: guard below pass vacuously forever. The committed corpus is 63 at the time of
#: writing; 40 leaves generous headroom for churn while still catching a
#: collapse to near-zero.
_MIN_EXPECTED_SIDECARS = 40


def _has_real_digest(record: dict) -> bool:
    """True when the sidecar claims a real ``sha256_original`` (not null/empty)."""

    value = record.get("sha256_original")
    return isinstance(value, str) and bool(value.strip())


def test_the_real_provider_corpus_is_discovered() -> None:
    """Guard: the parametrised checks below must not silently cover nothing."""

    assert REAL_PROVIDER_DIRS, (
        "No real-provider fixtures found; the integrity checks would be vacuous."
    )


@pytest.mark.parametrize("directory", REAL_PROVIDER_DIRS, ids=_relative)
def test_every_real_provider_fixture_has_a_capture_sidecar(directory: Path) -> None:
    sidecar = directory / "capture.json"
    assert sidecar.is_file(), (
        f"{_relative(directory)} is a real-provider fixture but has no capture.json. "
        "Capture it with scripts/capture_fixture.py and attribute it in "
        "tests/fixtures/ingest/README.md."
    )


@pytest.mark.parametrize("directory", REAL_PROVIDER_DIRS, ids=_relative)
def test_every_sidecar_declares_every_required_field(directory: Path) -> None:
    record = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_FIELDS if f not in record]
    assert not missing, f"{_relative(directory)}/capture.json is missing {missing}."
    # A genuinely unknown value is null, never a guess -- but url, the stored
    # hash and the trim method are always knowable, so they must be populated.
    for field in ("url", "sha256_stored", "trim_method"):
        assert record[field], f"{_relative(directory)}/capture.json: '{field}' must not be empty."
    assert record["url"].startswith("https://"), (
        f"{_relative(directory)}/capture.json: url must be the official https source."
    )
    assert record["robots_allowed"] in (True, False, None)


@pytest.mark.parametrize("directory", REAL_PROVIDER_DIRS, ids=_relative)
def test_sha256_stored_matches_the_committed_bytes(directory: Path) -> None:
    """The sidecar must describe the bytes that are actually committed."""

    source = _source_file(directory)
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    declared = json.loads((directory / "capture.json").read_text(encoding="utf-8"))["sha256_stored"]
    assert declared == actual, (
        f"{_relative(source)} has changed since it was captured. Update "
        f"capture.json: sha256_stored should be {actual} (and record why in "
        "trim_method)."
    )


@pytest.mark.parametrize("directory", REAL_PROVIDER_DIRS, ids=_relative)
def test_sidecars_carry_no_credentials(directory: Path) -> None:
    """Provenance is public metadata; a captured URL must carry no secret."""

    raw = (directory / "capture.json").read_text(encoding="utf-8").lower()
    for forbidden in ("authorization", "api_key", "apikey", "access_token", "password"):
        assert forbidden not in raw, f"{_relative(directory)}/capture.json mentions '{forbidden}'."


def test_synthetic_fixtures_are_exempt_and_stay_that_way() -> None:
    """The example corpus is synthetic: no provenance to record, none claimed."""

    synthetic = _fixture_dirs(real_providers=False)
    assert synthetic, "the synthetic example corpus should exist"
    for directory in synthetic:
        assert not (directory / "capture.json").exists(), (
            f"{_relative(directory)} is synthetic but claims capture provenance; "
            "a hand-written document must not assert it came from a real source."
        )


#: Wall-clock reads. Asserting a *committed* fixture's age against any of these
#: is the Q2-A time bomb the guard below forbids -- tree-wide, not just here.
_CLOCK_READ_ATTRS = frozenset({"now", "today", "utcnow", "time", "monotonic"})

#: A floor on the number of test modules discovered, so a glob that silently
#: matches nothing (a moved tree root, a rename) cannot make the guard pass
#: vacuously forever. The tree holds 127 at the time of writing; 60 leaves
#: generous headroom for churn while still catching a collapse to near-zero.
_MIN_EXPECTED_TEST_MODULES = 60


def _test_modules() -> list[Path]:
    """Every test module in the tree, derived from the filesystem rather than a
    hand-written list, so a freshness bomb in a *new* file is still in scope."""

    return [p for p in sorted(TESTS_ROOT.rglob("*.py")) if "__pycache__" not in p.parts]


def _ast_of(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _names_a_committed_sidecar(tree: ast.AST) -> bool:
    """True when a module uses ``capture.json`` as a path literal -- i.e. it
    loads the committed sidecar whose age must never be asserted.

    Only string *constants* whose filename is exactly ``capture.json`` count
    (``directory / "capture.json"``, ``glob("**/capture.json")``). A docstring
    that merely mentions the word has some other ``Path(...).name`` and is
    ignored -- this is why the guard is AST-based, not a substring scan.
    """

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if PurePosixPath(node.value.replace("\\", "/")).name == "capture.json":
                return True
    return False


def _clock_read_lines(tree: ast.AST) -> list[int]:
    """Line numbers where a module reads the wall clock (``datetime.now()``,
    ``time.time()`` ...). Attribute calls only, matching the established
    LiveFetcher guard: naming ``now`` in prose is not a clock read."""

    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _CLOCK_READ_ATTRS
    )


def _module_rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


TEST_MODULES = _test_modules()

#: The subset that actually loads a committed sidecar. A "fixture is recent"
#: assertion can only be written where the sidecar is read, so these are exactly
#: the modules the guard must forbid the clock in. Derived from the tree.
SIDECAR_READING_MODULES = [p for p in TEST_MODULES if _names_a_committed_sidecar(_ast_of(p))]


def test_no_committed_sidecar_age_is_asserted_against_the_wall_clock() -> None:
    """Pin the Q2-A decision **tree-wide**: no test that loads a committed
    ``capture.json`` may read the wall clock.

    This is the widened successor to the old this-module-only guard. Reading a
    sidecar and comparing its ``fetched_at`` to ``datetime.now()`` is the
    "fixture must be recent" time bomb -- it reddens CI on a calendar boundary
    rather than on a real defect, and makes an offline suite depend on the
    clock. Previously that could only be caught if planted in *this* file; a
    freshness bomb in any other test that reads a sidecar now fails here too.

    The scope is drawn precisely so legitimate *runtime* freshness tests keep
    passing: ``assess_staleness``, ``test_evidence_currency`` and
    ``test_adviser_stale_evidence`` assert decay against an INJECTED clock and
    never load a committed ``capture.json``, so they are out of scope. The check
    is AST-based, so prose that merely mentions "freshness" does not trip it --
    only a real clock call inside a sidecar-reading module counts.

    Limitation, stated rather than papered over: this catches a wall-clock read
    (``now``/``today``/``utcnow``/``time``/``monotonic``). Comparing a fixture's
    age to a hard-coded threshold date is a different, non-wall-clock check and
    is deliberately not covered here.
    """

    offenders = {
        _module_rel(path): lines
        for path in SIDECAR_READING_MODULES
        if (lines := _clock_read_lines(_ast_of(path)))
    }
    assert not offenders, (
        "These test modules load a committed capture.json AND read the wall "
        f"clock: {offenders}. A committed fixture's age must never be asserted "
        "against the clock (decision Q2-A); freshness is a runtime concern "
        "enforced by assess_staleness. Remove the clock read."
    )


def test_the_freshness_guard_is_not_vacuous() -> None:
    """The tree-wide guard above must actually be scanning something.

    Two ways it could silently rot into a no-op, both pinned here: the module
    glob could match (almost) nothing after a tree move, or no file could load a
    sidecar at all -- either would make the clock check pass forever.
    """

    assert len(TEST_MODULES) >= _MIN_EXPECTED_TEST_MODULES, (
        f"Only {len(TEST_MODULES)} test modules discovered under {TESTS_ROOT}; "
        "the freshness guard is scanning almost nothing. Has the tree root moved?"
    )
    # This very module loads capture.json, so it must be in scope by construction.
    assert Path(__file__).resolve() in SIDECAR_READING_MODULES
    assert len(SIDECAR_READING_MODULES) >= 5, (
        f"Only {len(SIDECAR_READING_MODULES)} sidecar-reading modules found; the "
        "guard would be watching almost nothing."
    )


def _prettier_ignores(relative_posix: str) -> bool:
    """Resolve ``.prettierignore`` for one path using gitignore semantics.

    Only the constructs this repository's ignore file actually uses are
    implemented -- trailing-directory globs, ``**`` prefixes and ``!``
    re-inclusion -- and the LAST matching pattern wins, which is the rule that
    makes ordering load-bearing here.
    """

    decision = False
    for raw in (REPO_ROOT / ".prettierignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        candidates = {pattern, f"**/{pattern}"}
        if pattern.endswith("/"):
            candidates |= {f"{pattern}**", f"**/{pattern}**"}
        if pattern.endswith("**"):
            candidates.add(pattern[:-2] + "*")
        if any(fnmatch(relative_posix, candidate) for candidate in candidates):
            decision = not negated
    return decision


def test_every_live_capture_is_exempt_from_prettier() -> None:
    """A live-derived capture is an evidence artefact, not source code.

    Prettier rewrites markup -- it respells ``<br/>`` as ``<br />`` -- so
    formatting a capture makes it stop being the bytes the page served. That
    collision is not hypothetical: it made the Prettier gate and the Python
    suite mutually unsatisfiable, since the capture failed ``format:check`` as
    committed and failed seven tests once formatted.

    This is a static check on the ignore file rather than a Prettier
    invocation, so it needs no Node toolchain and cannot skip silently.
    """

    for directory in REAL_PROVIDER_DIRS:
        source = _source_file(directory)
        relative = source.relative_to(REPO_ROOT).as_posix()
        assert _prettier_ignores(relative), (
            f"{relative} is a live-derived capture but Prettier would format it, "
            "which would rewrite the bytes it is supposed to preserve. Add it to "
            ".prettierignore."
        )


def test_the_prettier_exemption_is_scoped_to_captures_and_stays_scoped() -> None:
    """Positive controls, so the guard above cannot pass vacuously.

    The exemption must NOT swallow the hand-written corpus (which is faithful
    to nothing and should stay formatted), and must still cover the malformed
    fixture that has to remain invalid. The latter depends on ordering: the
    malformed rule sits after the ``example`` re-inclusion precisely so it
    still wins.
    """

    synthetic_sources = [
        _source_file(directory) for directory in _fixture_dirs(real_providers=False)
    ]
    assert synthetic_sources, "synthetic corpus missing; these controls would be vacuous"

    formatted, malformed_json = [], []
    for source in synthetic_sources:
        relative = source.relative_to(REPO_ROOT).as_posix()
        # Only the malformed *JSON* fixtures are exempt: Prettier cannot parse
        # invalid JSON, whereas its HTML and XML parsers tolerate the malformed
        # documents in those adapters, which therefore stay formatted.
        if "malformed" in relative and source.suffix == ".json":
            malformed_json.append(relative)
        else:
            formatted.append(relative)

    assert formatted, "no formattable synthetic fixture found; control would be vacuous"
    for relative in formatted:
        assert not _prettier_ignores(relative), (
            f"{relative} is hand-written, not captured, so it should stay formatted; "
            "the capture exemption has over-reached."
        )

    assert malformed_json, "no malformed JSON fixture found; control would be vacuous"
    for relative in malformed_json:
        assert _prettier_ignores(relative), (
            f"{relative} must stay Prettier-ignored so it remains invalid; the "
            "'example' re-inclusion has resurrected it."
        )


# --- Disclosure guard: a real original digest must say what it does NOT attest --
#
# ``sha256_original`` looks like a re-checkable link to the live page, but it is
# not one and never can be: the providers serve per-build markup, so a later
# fetch of an unchanged page yields a different digest. Leaving that unstated is
# an unsupported claim about our own evidence -- the exact defect class this
# product exists to prevent, pointed inward. Every sidecar that carries a real
# digest must therefore also carry a ``sha256_original_note`` disclosing the
# limit. A null digest attests nothing and needs no note.


def test_the_disclosure_guard_scans_a_plausible_non_empty_corpus() -> None:
    """Non-vacuity: the guard must reason about the real committed corpus.

    A glob that silently matches nothing -- a moved fixture root, a renamed
    file -- would make every per-sidecar assertion below pass over an empty
    set. Pin a floor on the population AND require that at least one sidecar
    actually carries a real digest, so the disclosure guard can never be
    satisfied by asserting nothing.
    """

    assert len(ALL_CAPTURE_SIDECARS) >= _MIN_EXPECTED_SIDECARS, (
        f"only {len(ALL_CAPTURE_SIDECARS)} capture.json sidecar(s) found under "
        f"{FIXTURE_ROOT} (expected >= {_MIN_EXPECTED_SIDECARS}; the committed "
        "corpus is 63). The glob is matching almost nothing, which would make "
        "the disclosure guard vacuous."
    )
    records = [json.loads(p.read_text(encoding="utf-8")) for p in ALL_CAPTURE_SIDECARS]
    assert any(_has_real_digest(r) for r in records), (
        "no sidecar carries a real sha256_original, so the disclosure guard "
        "would assert nothing. Expected several real digests in the corpus."
    )


@pytest.mark.parametrize(
    "sidecar",
    ALL_CAPTURE_SIDECARS,
    ids=lambda p: p.relative_to(FIXTURE_ROOT).as_posix(),
)
def test_a_real_original_digest_is_always_disclosed(sidecar: Path) -> None:
    """The load-bearing guard: a real digest must carry a non-empty note.

    Strip ``sha256_original_note`` from any sidecar that has a real
    ``sha256_original`` and this fails, naming the offending file.
    """

    record = json.loads(sidecar.read_text(encoding="utf-8"))
    if not _has_real_digest(record):
        pytest.skip("sha256_original is null/empty: nothing to disclose")

    note = record.get("sha256_original_note")
    assert isinstance(note, str) and note.strip(), (
        f"{_relative(sidecar)} carries a real sha256_original but no "
        "sha256_original_note. A bare digest reads as a re-checkable link to the "
        "live page; it is not one. Add a note stating what the digest does and "
        "does not attest (see the other providers' sidecars for the wording)."
    )


def test_a_null_original_digest_needs_no_disclosure() -> None:
    """Precision control: a null digest attests nothing, so it needs no note.

    Cloudflare's captures carry ``sha256_original: null``. The guard must accept
    them exactly as committed -- without a note -- or it would demand a
    disclosure about a digest that does not exist. Assert such a sidecar is
    present (so this control is not vacuous) and that it genuinely omits the
    note.
    """

    null_digest_sidecars = [
        p
        for p in ALL_CAPTURE_SIDECARS
        if not _has_real_digest(json.loads(p.read_text(encoding="utf-8")))
    ]
    assert null_digest_sidecars, (
        "expected at least one null-digest sidecar (Cloudflare); this precision "
        "control would otherwise be vacuous."
    )
    for sidecar in null_digest_sidecars:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        assert not record.get("sha256_original"), (
            f"{_relative(sidecar)} was expected to have a null/empty sha256_original."
        )
        assert "sha256_original_note" not in record, (
            f"{_relative(sidecar)} has no real digest yet carries a "
            "sha256_original_note; the guard must not require -- nor should the "
            "corpus volunteer -- a disclosure about a digest that does not exist."
        )


# --- Stored-digest disclosure: the tamper-evidence seal is not a fidelity link -
#
# ``sha256_stored`` is present on EVERY committed capture -- it is the digest of
# the bytes on disk, re-checked by recomputing the same hash
# (``test_sha256_stored_matches_the_committed_bytes`` above). That makes it a
# tamper-evidence seal, and a *circular* one: it is computed from the very bytes
# it later guards, so it establishes NO link to the live page and must never be
# read as a fidelity control. A bare digest invites exactly that misreading, so
# every capture derived from a live source must disclose what the seal does and
# does not attest. See "What a passing ingest fixture test attests" in
# ``docs/TEST_STRATEGY.md`` for the full account, including the loophole this
# guard cannot close: a red seal can be "fixed" by recomputing the hash instead
# of re-reconciling against live, and the committed bytes look identical either
# way. The guard enforces that the disclosure is PRESENT; only author discipline
# enforces that the reconciliation was actually re-run.
#
# The trigger is the corpus's own provenance marker, not a digest heuristic. A
# capture the authors declared ``synthetic`` is a hand-built negative fixture
# (see ``test_captures_declare_synthetic_provenance_where_it_applies`` in the AWS
# and GCP adapter suites): it represents no live page, carries a
# ``negative_fixture_note`` instead, and is exempt. Every OTHER capture -- every
# ``synthetic``-absent sidecar, including live captures whose whole-document
# ``sha256_original`` is null (Cloudflare) -- still seals real committed bytes and
# must carry the disclosure. Keying on ``sha256_original`` would wrongly exempt
# those null-original captures, whose stored seal is exactly as circular.


def _is_synthetic(record: dict) -> bool:
    """True for a declared synthetic negative fixture (exempt from the seal note)."""

    return record.get("synthetic") is True


def test_the_stored_disclosure_guard_partitions_a_real_corpus() -> None:
    """Non-vacuity + precision for the stored-seal guard below.

    The guard requires a note on every NON-synthetic capture and exempts every
    declared-synthetic one. Prove both halves reason about a real population: at
    least one non-synthetic capture exists (so the requirement is not vacuous) and
    at least one declared-synthetic capture exists (so the exemption is not
    vacuous -- the AWS/GCP adapter suites pin ``synthetic: True`` on their
    negative fixtures).
    """

    records = [json.loads(p.read_text(encoding="utf-8")) for p in ALL_CAPTURE_SIDECARS]
    assert any(not _is_synthetic(r) for r in records), (
        "no non-synthetic capture found; the stored-seal disclosure guard would require nothing."
    )
    assert any(_is_synthetic(r) for r in records), (
        "no declared-synthetic capture found; the stored-seal exemption would be vacuous."
    )


@pytest.mark.parametrize(
    "sidecar",
    ALL_CAPTURE_SIDECARS,
    ids=lambda p: p.relative_to(FIXTURE_ROOT).as_posix(),
)
def test_a_stored_digest_is_always_disclosed(sidecar: Path) -> None:
    """The load-bearing guard: a live-sourced capture must disclose its seal.

    Strip ``sha256_stored_note`` from any non-synthetic sidecar and this fails,
    naming the offending file. The note must say what the seal IS -- a
    tamper-evidence seal on the committed bytes -- so the word ``tamper-evidence``
    is required rather than merely a non-empty string.
    """

    record = json.loads(sidecar.read_text(encoding="utf-8"))
    if _is_synthetic(record):
        pytest.skip("declared synthetic negative fixture: no live seal to disclose")

    note = record.get("sha256_stored_note")
    assert isinstance(note, str) and note.strip(), (
        f"{_relative(sidecar)} carries a real sha256_stored but no "
        "sha256_stored_note. A bare stored digest reads as a re-checkable link to "
        "the live page; it is a tamper-evidence seal on the committed bytes and "
        "nothing more. Add a note saying so (see the other providers' sidecars "
        "for the wording)."
    )
    assert "tamper-evidence" in note, (
        f"{_relative(sidecar)} discloses sha256_stored but the note omits what the "
        "seal IS. State that it is a tamper-evidence seal on the committed bytes, "
        "not a link to the live page, never a fidelity control."
    )


# --- Dedup guard: a list-valued disclosure must not repeat the same entry ------
#
# Some sidecars carry list-of-object disclosure fields -- e.g.
# ``duplicate_live_blocks_not_retained`` and ``duplicate_live_blocks_retained``
# -- where each entry documents ONE distinct duplicated-block situation on the
# live page. A provenance disclosure is read literally, so the same entry
# appearing twice reads as TWO distinct problems where there is one: it
# overstates a disclosure, which in a file whose entire job is honest provenance
# is precisely the wrong direction to be wrong in.
#
# The field set is derived from the tree, not hand-listed: any top-level field
# whose value is a non-empty list of JSON objects is treated as a disclosure
# list and must carry no byte-identical duplicate. Hard-coding the two known
# field names would leave the next disclosure field someone adds unguarded,
# which is the whole point.


def _list_of_object_disclosure_fields(record: dict) -> dict[str, list]:
    """Every top-level field whose value is a non-empty list of JSON objects.

    This is the dynamic definition of a "list-valued disclosure field": we do
    not name the fields, we recognise their SHAPE, so a disclosure field added
    later is covered the moment it lands.
    """

    fields: dict[str, list] = {}
    for name, value in record.items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            fields[name] = value
    return fields


def _duplicate_entries(entries: list) -> list:
    """Entries that repeat an earlier one under key-sorted serialisation.

    Two entries are "the same" iff their canonical (sorted-key) JSON is
    byte-identical. This is a DUPLICATE check, not a "no two entries" ban: two
    entries that differ in any field are both kept.
    """

    seen: set[str] = set()
    duplicates: list = []
    for entry in entries:
        canonical = json.dumps(entry, sort_keys=True)
        if canonical in seen:
            duplicates.append(entry)
        else:
            seen.add(canonical)
    return duplicates


def _assert_dedup_corpus_is_non_vacuous(sidecars: list[Path]) -> None:
    """Shared non-vacuity check, so a probe can exercise it over an empty set.

    Fails when the sidecar glob matched (almost) nothing OR when the corpus
    holds no list-valued disclosure field at all -- either of which would make
    the dedup guard pass over an empty population forever.
    """

    assert len(sidecars) >= _MIN_EXPECTED_SIDECARS, (
        f"only {len(sidecars)} capture.json sidecar(s) found under {FIXTURE_ROOT} "
        f"(expected >= {_MIN_EXPECTED_SIDECARS}; the committed corpus is 63). The "
        "glob is matching almost nothing, which would make the dedup guard vacuous."
    )
    fields_found = 0
    for sidecar in sidecars:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        fields_found += len(_list_of_object_disclosure_fields(record))
    assert fields_found >= 1, (
        "no list-valued disclosure field found anywhere in the corpus, so the "
        "dedup guard would assert nothing. Expected several (e.g. "
        "duplicate_live_blocks_not_retained, duplicate_live_blocks_retained)."
    )


def test_the_dedup_guard_scans_list_valued_disclosure_fields() -> None:
    """Non-vacuity: the dedup guard must reason about real disclosure lists."""

    _assert_dedup_corpus_is_non_vacuous(ALL_CAPTURE_SIDECARS)


@pytest.mark.parametrize(
    "sidecar",
    ALL_CAPTURE_SIDECARS,
    ids=lambda p: p.relative_to(FIXTURE_ROOT).as_posix(),
)
def test_no_disclosure_list_contains_duplicate_entries(sidecar: Path) -> None:
    """The load-bearing guard: no disclosure list may repeat an entry.

    Re-insert a byte-identical entry into any list-valued disclosure field and
    this fails, naming the file and the field.
    """

    record = json.loads(sidecar.read_text(encoding="utf-8"))
    for field, entries in _list_of_object_disclosure_fields(record).items():
        duplicates = _duplicate_entries(entries)
        assert not duplicates, (
            f"{_relative(sidecar)}: disclosure field '{field}' repeats "
            f"{len(duplicates)} entry/entries verbatim. A provenance disclosure "
            "is read literally, so a duplicated entry overstates it (two problems "
            "where there is one). Remove the duplicate, or -- if the entries were "
            "meant to be distinct -- give each a distinguishing field."
        )


def test_the_dedup_guard_distinguishes_duplicates_from_distinct_entries() -> None:
    """Precision: it flags repeats, but two genuinely different entries PASS.

    Without this, the guard could be a "no list may hold two entries" ban rather
    than a duplicate check. Key order must not matter (canonicalisation), and a
    single differing field must be enough to keep both entries.
    """

    identical = [
        {"text": "x", "live_occurrences": 2, "retained_in_capture": 1},
        {"live_occurrences": 2, "retained_in_capture": 1, "text": "x"},
    ]
    assert _duplicate_entries(identical), (
        "two byte-identical entries (differing only in key order) must be reported as a duplicate."
    )

    distinct = [
        {"text": "x", "live_occurrences": 2, "retained_in_capture": 1},
        {"text": "y", "live_occurrences": 2, "retained_in_capture": 1},
    ]
    assert not _duplicate_entries(distinct), (
        "two entries that differ in any field are distinct disclosures and must "
        "both be kept; the guard is a duplicate check, not a two-entry ban."
    )
