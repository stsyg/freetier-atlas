"""The baseline refresher's REFUSE-TO-WRITE wiring, measured in both directions.

WHY THIS FILE EXISTS
--------------------
``scripts/refresh_secrets_baseline.py`` exists to make one failure impossible:
losing a ``.secrets.baseline`` entry silently. Its docstring says so -- "REFUSES
to write when a tracked file would disappear or a per-file entry count would
drop, restoring the original bytes instead" -- and its four shared predicates are
well covered by ``tests/unit/test_secrets_baseline.py``.

The **wiring** was not. Measured on this tree at 6e14a471 by mutation against the
WHOLE suite (3006 tests collected, all green first):

* changing ``if problems:`` to ``if problems and False:`` -- so the refresher
  writes a baseline the checker would reject -- left all 3006 tests GREEN;
* dropping the ``direction_problems`` call entirely -- so a per-file count drop
  passes -- also left all 3006 tests GREEN.

Both mutations restore the exact defect the wrapper was written to prevent, and
nothing observed them. The predicates were tested; the decision to *act* on them
was not. A grep for the script's name over ``tests/`` returns one hit, in a
docstring.

WHAT IS PINNED HERE
-------------------
Current behaviour, exactly as measured -- no rule of the refresher is changed.
Every case is PAIRED: a refusing arm and a permitting arm that differ in exactly
one input. A one-armed suite cannot tell a working guard from one that refuses
everything, and a refresher that refuses everything has broken the product just
as thoroughly as one that refuses nothing.

The real repository ``.secrets.baseline`` is never read or written: every test
runs against a temporary root, and the fixture ASSERTS the redirection took
effect rather than announcing it.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REAL_REPO_ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not an importable package; load by path, as test_secrets_baseline
# does, rather than widening pythonpath for one module.
_SPEC = importlib.util.spec_from_file_location(
    "refresh_secrets_baseline", REAL_REPO_ROOT / "scripts" / "refresh_secrets_baseline.py"
)
assert _SPEC and _SPEC.loader
refresher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(refresher)

BASELINE_NAME = refresher.BASELINE_NAME

#: Files the fixture baseline claims to have scanned. They are created on disk so
#: ``existence_problems`` has nothing to say and the DIRECTION check is what the
#: paired arms actually differ on.
TRACKED = ("alpha.txt", "nested/beta.txt")


def _digest(seed: str) -> str:
    """A well-formed 40-char lowercase SHA-1, computed rather than pasted.

    Never a literal: a 40-hex literal in this file would be a new detect-secrets
    finding, which would make the committed baseline stale -- this suite's own
    subject matter.
    """

    return hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()


def _entry(key: str, seed: str, line: int = 1) -> dict[str, Any]:
    return {
        "type": "Hex High Entropy String",
        "filename": key,
        "hashed_secret": _digest(seed),
        "is_verified": False,
        "line_number": line,
    }


def _baseline(results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "version": "1.5.0",
        "plugins_used": [{"name": "HexHighEntropyStringDetector"}],
        "filters_used": [{"path": "detect_secrets.filters.heuristic.is_potential_uuid"}],
        "results": results,
        "generated_at": "2026-01-01T00:00:00Z",
    }


ORIGINAL_RESULTS: dict[str, list[dict[str, Any]]] = {
    "alpha.txt": [_entry("alpha.txt", "a1", 3), _entry("alpha.txt", "a2", 9)],
    "nested/beta.txt": [_entry("nested/beta.txt", "b1", 4)],
}


class _Root:
    """A temporary repository root standing in for the real one."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.baseline = path / BASELINE_NAME

    def bytes(self) -> bytes:
        return self.baseline.read_bytes()

    def results(self) -> dict[str, Any]:
        return json.loads(self.baseline.read_text(encoding="utf-8"))["results"]


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Root]:
    for name in TRACKED:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder\n", encoding="utf-8")

    baseline = tmp_path / BASELINE_NAME
    # LF and write_bytes deliberately: the committed baseline is LF-only, and a
    # CRLF fixture would make the "already current" case differ from a correct
    # refresh for a reason that has nothing to do with the guard under test.
    baseline.write_bytes((json.dumps(_baseline(ORIGINAL_RESULTS), indent=2) + "\n").encode("utf-8"))
    assert b"\r\n" not in baseline.read_bytes()

    monkeypatch.setattr(refresher, "REPO_ROOT", tmp_path)

    # Environment precondition ASSERTED, not announced. If the redirection ever
    # stopped working these tests would refresh the real committed baseline, and
    # a green run would mean nothing.
    assert refresher.REPO_ROOT == tmp_path
    assert refresher.REPO_ROOT != REAL_REPO_ROOT
    # Vacuity floor: the direction check needs more than one file and more than
    # one entry to have anything to bite on.
    assert len(ORIGINAL_RESULTS) >= 2
    assert sum(len(v) for v in ORIGINAL_RESULTS.values()) >= 3

    yield _Root(tmp_path)


def _stub_scan(
    monkeypatch: pytest.MonkeyPatch,
    scanned: dict[str, list[dict[str, Any]]] | None,
    *,
    code: int = 0,
) -> None:
    """Stand in for ``detect-secrets scan --baseline``, which rewrites in place.

    The real command's defining behaviour -- and the whole reason this wrapper
    exists -- is that it overwrites the baseline and exits 0 silently. The stub
    reproduces exactly that, so what is under test is the wrapper's reaction.
    """

    def _run(baseline_path: Path, root_path: Path) -> int:
        if scanned is not None:
            baseline_path.write_text(
                json.dumps(_baseline(scanned), indent=2) + "\n", encoding="utf-8"
            )
        return code

    monkeypatch.setattr(refresher, "run_scan", _run)


def _grown() -> dict[str, list[dict[str, Any]]]:
    """A legitimate refresh: one new tracked file, one new entry, none lost."""

    grown = copy.deepcopy(ORIGINAL_RESULTS)
    grown["alpha.txt"].append(_entry("alpha.txt", "a3", 21))
    grown["nested/gamma.txt"] = [_entry("nested/gamma.txt", "g1", 2)]
    return grown


def _file_dropped() -> dict[str, list[dict[str, Any]]]:
    """The mode-A wipe: a tracked file vanishes from the results."""

    dropped = copy.deepcopy(ORIGINAL_RESULTS)
    del dropped["nested/beta.txt"]
    return dropped


def _count_shrunk() -> dict[str, list[dict[str, Any]]]:
    """The subtler mode-A loss: the file stays, one of its entries does not."""

    shrunk = copy.deepcopy(ORIGINAL_RESULTS)
    shrunk["alpha.txt"] = shrunk["alpha.txt"][:1]
    return shrunk


# --- the refusing direction (a silent loss must never be written) ------------


def test_a_refresh_that_drops_a_tracked_file_is_refused_and_restores_the_original(
    root: _Root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = root.bytes()
    _stub_scan(monkeypatch, _file_dropped())

    assert refresher.main([]) == 1
    # Byte-for-byte, not json-equal: the promise in the docstring is about bytes.
    assert root.bytes() == before
    assert "REFUSED" in capsys.readouterr().err


def test_a_refresh_that_shrinks_an_entry_count_is_refused(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = root.bytes()
    _stub_scan(monkeypatch, _count_shrunk())

    assert refresher.main([]) == 1
    assert root.bytes() == before


def test_a_key_naming_a_file_that_no_longer_exists_is_refused(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = root.bytes()
    stale = copy.deepcopy(ORIGINAL_RESULTS)
    stale["deleted/vanished.txt"] = [_entry("deleted/vanished.txt", "v1")]
    _stub_scan(monkeypatch, stale)

    assert refresher.main([]) == 1
    assert root.bytes() == before


def test_a_malformed_digest_is_refused(root: _Root, monkeypatch: pytest.MonkeyPatch) -> None:
    before = root.bytes()
    malformed = copy.deepcopy(ORIGINAL_RESULTS)
    # A real digest with one character removed, built at run time. Deliberately
    # not a quoted stand-in beside the word "hashed_secret": that shape is itself
    # a detect-secrets Secret Keyword finding, which would make the committed
    # baseline stale and turn this suite into the problem it audits.
    truncated = _digest("a1")[:-1]
    assert len(truncated) == 39
    malformed["alpha.txt"][0]["hashed_secret"] = truncated
    _stub_scan(monkeypatch, malformed)

    assert refresher.main([]) == 1
    assert root.bytes() == before


def test_a_failing_scan_restores_the_original_and_reports_the_exit_code(
    root: _Root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = root.bytes()
    # A scan that both corrupts the file AND fails: the wrapper must not keep the
    # corruption just because it also reports the failure.
    _stub_scan(monkeypatch, _file_dropped(), code=3)

    assert refresher.main([]) == 1
    assert root.bytes() == before
    assert "exited 3" in capsys.readouterr().err


def test_a_missing_baseline_is_reported_rather_than_created(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    root.baseline.unlink()
    _stub_scan(monkeypatch, _grown())

    assert refresher.main([]) == 1
    assert not root.baseline.exists()


# --- the permitting direction (a refresher that refuses everything is broken) -


def test_a_legitimate_growth_refresh_is_written(
    root: _Root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The arm without which every refusal above would prove nothing."""

    (root.path / "nested" / "gamma.txt").write_text("placeholder\n", encoding="utf-8")
    before = root.bytes()
    _stub_scan(monkeypatch, _grown())

    assert refresher.main([]) == 0
    assert root.bytes() != before
    results = root.results()
    assert set(results) == {"alpha.txt", "nested/beta.txt", "nested/gamma.txt"}
    assert len(results["alpha.txt"]) == 3
    assert "CHANGED" in capsys.readouterr().out


def test_an_identical_refresh_reports_already_current_and_changes_nothing(
    root: _Root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = root.bytes()
    _stub_scan(monkeypatch, copy.deepcopy(ORIGINAL_RESULTS))

    assert refresher.main([]) == 0
    assert root.bytes() == before
    assert "already current" in capsys.readouterr().out


def test_allow_removals_permits_exactly_the_loss_that_is_otherwise_refused(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented escape hatch, pinned so it stays an EXPLICIT decision."""

    _stub_scan(monkeypatch, _file_dropped())

    assert refresher.main(["--allow-removals"]) == 0
    assert set(root.results()) == {"alpha.txt"}


def test_dry_run_restores_the_original_even_though_it_would_change(
    root: _Root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (root.path / "nested" / "gamma.txt").write_text("placeholder\n", encoding="utf-8")
    before = root.bytes()
    _stub_scan(monkeypatch, _grown())

    assert refresher.main(["--dry-run"]) == 0
    assert root.bytes() == before
    assert "WOULD change" in capsys.readouterr().out


# --- what the wrapper normalises on the way out -----------------------------


def test_a_windows_backslash_scan_is_normalised_to_posix_on_write(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The corruption this wrapper was written for: keys rewritten with ``\\``.

    Normalisation must happen BEFORE the checks, or a backslashed key would read
    as "the posix key disappeared" and the refresh would be refused for the wrong
    reason -- turning a fixable rewrite into a permanent refusal.
    """

    windows = {
        "alpha.txt": copy.deepcopy(ORIGINAL_RESULTS["alpha.txt"]),
        "nested\\beta.txt": [_entry("nested\\beta.txt", "b1", 4)],
    }
    _stub_scan(monkeypatch, windows)

    assert refresher.main([]) == 0
    results = root.results()
    assert set(results) == {"alpha.txt", "nested/beta.txt"}
    assert results["nested/beta.txt"][0]["filename"] == "nested/beta.txt"


def test_the_written_baseline_is_lf_only_and_ends_with_a_newline(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root.path / "nested" / "gamma.txt").write_text("placeholder\n", encoding="utf-8")
    _stub_scan(monkeypatch, _grown())

    assert refresher.main([]) == 0
    raw = root.bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_the_injected_baseline_filter_is_dropped_on_write(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _run(baseline_path: Path, root_path: Path) -> int:
        doc = _baseline(copy.deepcopy(ORIGINAL_RESULTS))
        doc["filters_used"].append({"path": refresher.INJECTED_FILTER})
        doc["results"]["alpha.txt"].append(_entry("alpha.txt", "a3", 21))
        baseline_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(refresher, "run_scan", _run)

    assert refresher.main([]) == 0
    written = json.loads(root.baseline.read_text(encoding="utf-8"))
    paths = [f.get("path") for f in written["filters_used"]]
    assert refresher.INJECTED_FILTER not in paths
    assert paths, "every filter was dropped, not just the injected one"


def test_no_backup_sidecar_survives_either_outcome(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar = root.baseline.with_suffix(root.baseline.suffix + ".orig")

    _stub_scan(monkeypatch, _file_dropped())
    assert refresher.main([]) == 1
    assert not sidecar.exists()

    (root.path / "nested" / "gamma.txt").write_text("placeholder\n", encoding="utf-8")
    _stub_scan(monkeypatch, _grown())
    assert refresher.main([]) == 0
    assert not sidecar.exists()


# --- the control that makes the two arms mean something ---------------------


def test_the_refused_and_permitted_inputs_differ_only_in_direction(
    root: _Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape, opposite direction, opposite verdict -- and the delta is named.

    Without this, "refused" and "permitted" could both be true for reasons that
    have nothing to do with the direction check: a suite that refuses a dropped
    file and accepts a grown one is equally consistent with a wrapper keyed on
    the number of files, on the presence of ``gamma``, or on the phase of the
    moon. Here the predicate that separates them is called directly and shown to
    be the direction check itself, with the accepting side proved silent.
    """

    dropped = _file_dropped()
    grown = _grown()

    drop_problems = refresher.direction_problems(dropped, ORIGINAL_RESULTS)
    grow_problems = refresher.direction_problems(grown, ORIGINAL_RESULTS)

    assert len(drop_problems) == 1
    assert "nested/beta.txt" in drop_problems[0]
    assert "DISAPPEARED" in drop_problems[0]
    assert grow_problems == []

    shrink_problems = refresher.direction_problems(_count_shrunk(), ORIGINAL_RESULTS)
    assert len(shrink_problems) == 1
    assert "DECREASED" in shrink_problems[0]

    # And the wrapper's verdicts follow that predicate, not something incidental:
    # identical stub, identical root, one differing input, opposite exit codes.
    (root.path / "nested" / "gamma.txt").write_text("placeholder\n", encoding="utf-8")
    _stub_scan(monkeypatch, grown)
    permitted = refresher.main([])
    _stub_scan(monkeypatch, dropped)
    refused = refresher.main([])
    assert (permitted, refused) == (0, 1)
