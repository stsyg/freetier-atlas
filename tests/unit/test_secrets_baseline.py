"""Permanent reproductions of the four ``.secrets.baseline`` corruptions.

``.secrets.baseline`` has been corrupted four times by four different code paths,
twice after the failure was documented at high confidence and read by the person
who then hit it. These tests exist because prose demonstrably did not work: they
turn each historical corruption into a red test.

Two of them are reproduced from measured artefacts rather than imagination:

* **Mode B, backslash rewrite.** Measured on Windows: shifting one fixture's line
  numbers makes ``detect-secrets-hook`` exit 3 and rewrite all 21 result keys with
  backslashes while preserving all 75 entries. ``detect-secrets scan --baseline``
  does the same at exit 0, silently. The shape asserted here - every key and every
  entry ``filename`` backslashed, counts untouched - is that artefact's shape.
* **Mode A, silent wipe.** Entries vanish instead of updating, because a refresh
  keyed by native path does not match the committed posix keys.

The single most important test in this file is
:func:`test_deletion_is_a_change_and_must_still_fail`. A previous guard asserted
that the right entries had *changed* and PASSED on a wipe, because a deletion is a
change. It constrained scope but not DIRECTION. That is the defect this suite is
built to make impossible to reintroduce.

The second most important is :func:`test_legitimate_in_place_refresh_passes`. A
check that fires on correct work teaches people to bypass it, which is worse than
no check at all.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / ".secrets.baseline"

# scripts/ is not an importable package, so load the validator by path rather than
# widening pythonpath for one module.
_SPEC = importlib.util.spec_from_file_location(
    "check_secrets_baseline", REPO_ROOT / "scripts" / "check_secrets_baseline.py"
)
assert _SPEC and _SPEC.loader
validator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validator)

# A file with many entries, so a count reduction is expressible without emptying it.
MULTI_ENTRY_FILE = "tests/fixtures/ingest/github/html/github-pages-limits/capture.json"


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def results(committed: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return copy.deepcopy(committed["results"])


def structural_problems(results: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Every reference-free check the build runs, in one call."""
    return (
        validator.posix_key_problems(results)
        + validator.entry_problems(results)
        + validator.existence_problems(results, REPO_ROOT)
    )


def backslashed(results: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Reproduce the measured mode-B artefact: posix separators flipped, counts kept."""
    corrupted: dict[str, list[dict[str, Any]]] = {}
    for key, entries in results.items():
        native = key.replace("/", "\\")
        rewritten = []
        for entry in entries:
            entry = dict(entry)
            if "filename" in entry:
                entry["filename"] = entry["filename"].replace("/", "\\")
            rewritten.append(entry)
        corrupted[native] = rewritten
    return corrupted


# --------------------------------------------------------------------------
# Positive control. Run first in spirit: a failure reported below only means
# something if the validator passes on the file as committed.
# --------------------------------------------------------------------------


def test_committed_baseline_passes_every_structural_check(results) -> None:
    assert structural_problems(results) == []


def test_committed_baseline_is_self_consistent(results) -> None:
    assert results, "the committed baseline has no results at all"
    for key, entries in results.items():
        assert "\\" not in key
        assert entries, f"{key} has an empty entry list"
        for entry in entries:
            assert validator.SHA1_RE.match(entry["hashed_secret"])


def test_committed_baseline_survives_the_directional_check_against_itself(results) -> None:
    assert validator.direction_problems(results, copy.deepcopy(results)) == []


# --------------------------------------------------------------------------
# Mode B - the backslash rewrite.
# --------------------------------------------------------------------------


def test_mode_b_backslash_keys_fail(results) -> None:
    problems = validator.posix_key_problems(backslashed(results))
    assert len(problems) == len(results)
    assert all("backslash" in problem for problem in problems)


def test_mode_b_is_invisible_to_the_directional_check(results) -> None:
    """Why the posix check cannot be replaced by a count check.

    Mode B preserves every file and every count, so direction alone sees nothing.
    Both check families are load-bearing; neither is redundant.
    """
    corrupted = backslashed(results)
    assert sum(len(v) for v in corrupted.values()) == sum(len(v) for v in results.values())
    assert validator.direction_problems(corrupted, results) == []
    assert validator.posix_key_problems(corrupted) != []


def test_mode_b_entry_filenames_are_also_checked(results) -> None:
    """A key repaired without its entries would still be inconsistent."""
    half_fixed = {k: v for k, v in backslashed(results).items()}
    key = next(iter(half_fixed))
    half_fixed[key.replace("\\", "/")] = half_fixed.pop(key)
    assert validator.entry_problems(half_fixed) != []


# --------------------------------------------------------------------------
# Mode A - the silent wipe.
# --------------------------------------------------------------------------


def test_mode_a_total_wipe_fails(results) -> None:
    problems = validator.direction_problems({}, results)
    assert len(problems) == len(results)
    assert all("DISAPPEARED" in problem for problem in problems)


def test_mode_a_single_file_removed_fails(results) -> None:
    candidate = copy.deepcopy(results)
    del candidate[MULTI_ENTRY_FILE]
    problems = validator.direction_problems(candidate, results)
    assert len(problems) == 1
    assert "DISAPPEARED" in problems[0]


def test_mode_a_reduced_entry_count_fails(results) -> None:
    candidate = copy.deepcopy(results)
    before = len(candidate[MULTI_ENTRY_FILE])
    candidate[MULTI_ENTRY_FILE] = candidate[MULTI_ENTRY_FILE][:-1]
    problems = validator.direction_problems(candidate, results)
    assert len(problems) == 1
    assert f"DECREASED from {before} to {before - 1}" in problems[0]


def test_deletion_is_a_change_and_must_still_fail(results) -> None:
    """The regression test for the guard that passed on a wipe.

    Every surviving entry here is byte-identical to the reference; the only
    difference is that one file is gone. A guard asserting that the right entries
    CHANGED is satisfied by this. A guard asserting DIRECTION is not.
    """
    candidate = copy.deepcopy(results)
    removed = candidate.pop(MULTI_ENTRY_FILE)
    assert candidate != results, "a deletion is indeed a change"
    assert all(candidate[k] == results[k] for k in candidate), "nothing else was touched"
    assert removed, "the removed file really did carry entries"
    assert validator.direction_problems(candidate, results) != []


# --------------------------------------------------------------------------
# False positives. A check that fires on correct work gets bypassed.
# --------------------------------------------------------------------------


def test_legitimate_in_place_refresh_passes(results) -> None:
    """A real refresh moves a line number and re-hashes a digest. Nothing is lost.

    Measured end to end: a full Windows refresh through
    ``scripts/refresh_secrets_baseline.py`` produced a file differing from the
    committed one ONLY in ``generated_at``, with ``results`` byte-identical.
    """
    candidate = copy.deepcopy(results)
    entry = candidate[MULTI_ENTRY_FILE][0]
    entry["line_number"] = entry.get("line_number", 1) + 7
    entry["hashed_secret"] = "0" * 39 + "a"
    assert structural_problems(candidate) == []
    assert validator.direction_problems(candidate, results) == []


def test_growth_passes(results) -> None:
    """New findings and new files are normal. The check is non-decreasing."""
    candidate = copy.deepcopy(results)
    extra = dict(candidate[MULTI_ENTRY_FILE][0])
    extra["line_number"] = 9999
    extra["hashed_secret"] = "c" * 40
    candidate[MULTI_ENTRY_FILE].append(extra)
    candidate["README.md"] = [
        {
            "filename": "README.md",
            "hashed_secret": "b" * 40,
            "is_verified": False,
            "line_number": 3,
            "type": "Hex High Entropy String",
        }
    ]
    assert structural_problems(candidate) == []
    assert validator.direction_problems(candidate, results) == []


def test_generated_at_churn_never_fails(committed: dict[str, Any]) -> None:
    """Argued in check_secrets_baseline.advisory_notes: informational, never a gate."""
    regenerated = copy.deepcopy(committed)
    regenerated["generated_at"] = "2099-01-01T00:00:00Z"
    notes = validator.advisory_notes(regenerated, committed)
    assert any("generated_at changed" in note for note in notes)
    assert structural_problems(regenerated["results"]) == []


# --------------------------------------------------------------------------
# Remaining structural invariants.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_digest",
    [
        "",
        "NOTAHASH",
        # Uppercase hex is a valid SHA-1 SHAPE but not the lowercase detect-secrets
        # writes, so it must still be rejected. It is a test constant, not a secret.
        "ABCDEF0123456789ABCDEF0123456789ABCDEF01",  # pragma: allowlist secret
        "0" * 39,
        "0" * 41,
    ],
)
def test_malformed_hashed_secret_fails(results, bad_digest: str) -> None:
    candidate = copy.deepcopy(results)
    candidate[MULTI_ENTRY_FILE][0]["hashed_secret"] = bad_digest
    assert validator.entry_problems(candidate) != []


@pytest.mark.parametrize(
    "bad_key",
    [
        "/etc/passwd",
        "C:/repo/config.yaml",
        "./config/examples/llm-providers.example.yaml",
        "../outside/file.json",
    ],
)
def test_non_relative_posix_keys_fail(results, bad_key: str) -> None:
    candidate = copy.deepcopy(results)
    candidate[bad_key] = candidate.pop(MULTI_ENTRY_FILE)
    assert validator.posix_key_problems(candidate) != []


def test_stale_entry_for_a_deleted_file_fails(results) -> None:
    """The one corruption the detect-secrets hook accepts at exit 0.

    A deleted file is absent from ``git ls-files``, so the hook never scans it and
    never trims its entry. Only this check sees it.
    """
    candidate = copy.deepcopy(results)
    candidate["tests/fixtures/ingest/deleted/gone.json"] = [
        {
            "filename": "tests/fixtures/ingest/deleted/gone.json",
            "hashed_secret": "d" * 40,
            "is_verified": False,
            "line_number": 1,
            "type": "Hex High Entropy String",
        }
    ]
    assert validator.existence_problems(candidate, REPO_ROOT) != []


def test_empty_entry_list_fails(results) -> None:
    candidate = copy.deepcopy(results)
    candidate[MULTI_ENTRY_FILE] = []
    assert validator.entry_problems(candidate) != []


# --------------------------------------------------------------------------
# Wiring. A guard nobody runs is not a guard.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_path",
    [
        ".github/workflows/ci.yml",
        ".pre-commit-config.yaml",
        "scripts/check.ps1",
        "scripts/check.sh",
    ],
)
def test_every_route_invokes_the_validator(relative_path: str) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "check_secrets_baseline" in text, (
        f"{relative_path} no longer runs the secrets baseline validator; "
        "the corruption this suite reproduces would ship undetected from that route"
    )


# --------------------------------------------------------------------------
# The file list must be NUL-delimited. Measured on Linux with a secret planted
# in 'q2 dir/has space.txt': `... $(git ls-files)` word-splits and exits 0 - a
# silent pass on a real secret - while `xargs -0` catches it but collapses the
# exit code into 123, losing the 1-versus-3 distinction. Reading NUL-delimited
# names into an array is the only form that keeps both properties.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relative_path", [".github/workflows/ci.yml", "scripts/check.sh"])
def test_shell_routes_read_the_file_list_nul_delimited(relative_path: str) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "read -r -d ''" in text and "git ls-files -z" in text, (
        f"{relative_path} must read tracked filenames NUL-delimited into an array"
    )
    assert 'detect-secrets-hook --baseline .secrets.baseline "${files[@]}"' in text or (
        '--baseline .secrets.baseline "${files[@]}"' in text
    ), f"{relative_path} must pass the array quoted, or a whitespace filename is skipped"


@pytest.mark.parametrize("relative_path", [".github/workflows/ci.yml", "scripts/check.sh"])
def test_shell_routes_do_not_word_split_the_file_list(relative_path: str) -> None:
    """The regression the evaluator measured: a planted secret passing at exit 0."""
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    for forbidden in ("$(git ls-files)", "`git ls-files`"):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            assert forbidden not in stripped, (
                f"{relative_path} word-splits the file list; a tracked filename "
                f"containing whitespace would be silently skipped: {stripped!r}"
            )


@pytest.mark.parametrize("relative_path", [".github/workflows/ci.yml", "scripts/check.sh"])
def test_shell_routes_avoid_mapfile(relative_path: str) -> None:
    """`mapfile` does not exist on bash 3.2, still the system bash on macOS.

    Verified in containers: 3.2.57 has no `mapfile` builtin at all, while the
    `while read -r -d ''` loop behaves identically on 3.2.57 and 5.2.37.
    """
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert "mapfile" not in stripped, f"{relative_path} uses mapfile, absent on bash 3.2"


# --------------------------------------------------------------------------
# --require-reference must not be satisfiable by the commit under validation.
# Measured: with a genuine mode-A corruption COMMITTED, a HEAD-resolved run
# reported "passed... nothing lost", because it compared the file with itself.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rev", ["HEAD", "@", " HEAD "])
def test_head_is_recognised_as_a_self_reference(rev: str) -> None:
    assert validator.is_self_reference(rev)


@pytest.mark.parametrize("rev", ["origin/main", "7577b619", "main", "HEAD~1"])
def test_a_real_revision_is_not_a_self_reference(rev: str) -> None:
    """A resolved SHA that merely coincides with HEAD must stay usable.

    On a push to `main` the merge base legitimately IS the pushed commit, and
    rejecting that would break the default branch for a healthy no-op change.
    """
    assert not validator.is_self_reference(rev)


def test_explicit_head_is_refused_under_require_reference() -> None:
    assert validator.resolve_reference("HEAD", REPO_ROOT, require_reference=True) is None


def test_head_is_still_allowed_without_require_reference() -> None:
    resolved = validator.resolve_reference("HEAD", REPO_ROOT, require_reference=False)
    assert resolved is not None and resolved[0] == "HEAD"


def test_require_reference_never_resolves_to_head_in_this_repository() -> None:
    resolved = validator.resolve_reference(None, REPO_ROOT, require_reference=True)
    if resolved is not None:
        assert not validator.is_self_reference(resolved[0])


def test_require_reference_fails_when_nothing_can_be_resolved(tmp_path) -> None:
    """End to end, outside any git repository: the flag must fail closed.

    Previously the candidate list ended with the literal `HEAD`, which nearly
    always resolves, so this flag could never fire.
    """
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    baseline = {
        "version": "1.5.0",
        "plugins_used": [],
        "filters_used": [],
        "results": {
            "a.txt": [
                {
                    "filename": "a.txt",
                    "hashed_secret": "e" * 40,
                    "is_verified": False,
                    "line_number": 1,
                    "type": "Hex High Entropy String",
                }
            ]
        },
    }
    path = tmp_path / ".secrets.baseline"
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_secrets_baseline.py"),
            "--baseline",
            str(path),
            "--repo-root",
            str(tmp_path),
            "--require-reference",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "no reference baseline could be resolved" in completed.stderr


def test_without_the_flag_a_missing_reference_only_skips(tmp_path) -> None:
    """The permissive path must stay usable, and must say so out loud."""
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    baseline = {
        "version": "1.5.0",
        "plugins_used": [],
        "filters_used": [],
        "results": {
            "a.txt": [
                {
                    "filename": "a.txt",
                    "hashed_secret": "e" * 40,
                    "is_verified": False,
                    "line_number": 1,
                    "type": "Hex High Entropy String",
                }
            ]
        },
    }
    path = tmp_path / ".secrets.baseline"
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_secrets_baseline.py"),
            "--baseline",
            str(path),
            "--repo-root",
            str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SKIPPED" in completed.stdout
