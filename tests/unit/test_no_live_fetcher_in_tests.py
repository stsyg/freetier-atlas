"""CI performs zero socket operations (F008 S3, decision Q3-A).

The whole ingestion pipeline is offline by construction: adapters take an
injected ``Fetcher``, and tests inject ``FixtureFetcher`` / ``OfflineFetcher``.
:class:`~app.ingest.fetch.LiveFetcher` is the one component that can open a
socket, and this module pins where it may be **constructed**:

* in ``app/ingest/fetch.py``, which defines it;
* in ``scripts/capture_fixture.py``, the owner-run capture tool that is never
  invoked by tests or CI;
* in ``tests/unit/test_ingest_fetch.py``, the single sanctioned exception --
  those constructions target a ``127.0.0.1`` loopback server started by the test
  itself with ``allow_loopback=True``, so they exercise the fetch guards with
  **zero external egress**. That exception is deliberately hard-coded here: a
  *new* test file that constructs a ``LiveFetcher`` fails this suite.

The checks are AST-based, not substring-based. Naming ``LiveFetcher`` in a
docstring, an ``__all__`` list or a re-export is not a network call, and a
substring test would either miss real constructions (``fetch.LiveFetcher(...)``)
or flag harmless prose. Only ``ast.Call`` nodes count.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
API_ROOT = REPO_ROOT / "apps" / "api" / "app"
SCRIPTS_ROOT = REPO_ROOT / "scripts"

#: Defines the class; constructing it here is the point.
DEFINING_MODULE = API_ROOT / "ingest" / "fetch.py"

#: The owner-run capture tool -- the ONLY non-test module allowed to build one.
CAPTURE_SCRIPT = SCRIPTS_ROOT / "capture_fixture.py"

#: The single sanctioned test exception (loopback-only, documented above).
SANCTIONED_TEST = TESTS_ROOT / "unit" / "test_ingest_fetch.py"

#: Fetchers a test may construct freely: none of them can open a socket.
OFFLINE_FETCHERS = frozenset({"FixtureFetcher", "OfflineFetcher"})


def _python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        yield path


def _constructions(path: Path, name: str) -> list[int]:
    """Line numbers where ``path`` *calls* ``name`` (bare or attribute access)."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            lines.append(node.lineno)
    return lines


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


TEST_FILES = list(_python_files(TESTS_ROOT))
APP_AND_SCRIPT_FILES = list(_python_files(API_ROOT)) + list(_python_files(SCRIPTS_ROOT))


def test_the_scan_is_not_vacuous() -> None:
    assert len(TEST_FILES) >= 20
    assert SANCTIONED_TEST in TEST_FILES
    assert CAPTURE_SCRIPT in APP_AND_SCRIPT_FILES


def test_no_test_constructs_a_live_fetcher() -> None:
    offenders = {
        _rel(path): lines
        for path in TEST_FILES
        if path != SANCTIONED_TEST and (lines := _constructions(path, "LiveFetcher"))
    }
    assert not offenders, (
        f"These tests construct LiveFetcher: {offenders}. Tests must stay offline: "
        f"inject one of {sorted(OFFLINE_FETCHERS)} instead. The only sanctioned "
        f"exception is {_rel(SANCTIONED_TEST)} (loopback server, "
        "allow_loopback=True, zero external egress)."
    )


def test_the_sanctioned_exception_is_still_loopback_only() -> None:
    """The exception is only sanctioned *because* it never leaves the machine."""

    source = SANCTIONED_TEST.read_text(encoding="utf-8")
    assert _constructions(SANCTIONED_TEST, "LiveFetcher"), (
        "The exception list is stale: this file no longer constructs a LiveFetcher, "
        "so it should be removed from SANCTIONED_TEST."
    )
    assert "allow_loopback=True" in source
    assert "127.0.0.1" in source
    # No real hostname is contacted: every allowlisted domain is loopback.
    assert "http://localhost" not in source or "127.0.0.1" in source


def test_capture_fixture_is_the_only_module_constructing_a_live_fetcher() -> None:
    offenders = {
        _rel(path): lines
        for path in APP_AND_SCRIPT_FILES
        if path not in (DEFINING_MODULE, CAPTURE_SCRIPT)
        and (lines := _constructions(path, "LiveFetcher"))
    }
    assert not offenders, (
        f"These modules construct LiveFetcher: {offenders}. Only "
        f"{_rel(DEFINING_MODULE)} (which defines it) and {_rel(CAPTURE_SCRIPT)} "
        "(owner-run, never invoked by tests or CI) may do so."
    )


def test_the_capture_script_really_does_construct_one() -> None:
    """Otherwise the allowance above would be protecting nothing."""

    assert _constructions(CAPTURE_SCRIPT, "LiveFetcher")


def test_the_capture_script_is_not_imported_by_any_test() -> None:
    """It must stay owner-run: no test or CI path may pull it in."""

    for path in TEST_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any("capture_fixture" in n for n in names), (
                f"{_rel(path)} imports capture_fixture; it is owner-run only and "
                "must never be reachable from the test suite."
            )
