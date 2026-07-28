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
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "ingest"

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


def test_no_freshness_assertion_exists_in_this_module() -> None:
    """Pin the Q2-A decision itself, so it cannot be quietly reintroduced.

    CI must never assert that a fixture is *recent*. This is an AST check, not a
    substring check, so prose explaining the rule does not trip it -- only an
    actual clock read does.
    """

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    clock_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"now", "today", "utcnow", "time", "monotonic"}
    ]
    assert not clock_reads, (
        "This module must not read the clock (line "
        f"{clock_reads[0].lineno if clock_reads else '?'}). Fixture freshness is a "
        "runtime concern enforced by assess_staleness, not a CI assertion "
        "(decision Q2-A)."
    )
