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

The wiring section at the end guards a third instance of the same family. It
used to assert ``"check_secrets_baseline" in text`` for each of the four routes.
Measured: that goes red when the invocation is DELETED and stays green when it
is COMMENTED OUT - and commenting out is the likelier bypass, because it is what
a contributor does to a check they believe is misfiring. A substring constrains
PRESENCE; what the guard owes us is EFFECT.
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

from tests.support import source_scan

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
# Wiring. A guard nobody runs is not a guard - and an invocation nobody
# executes is not an invocation.
#
# PRESENT-BUT-INERT MODES CONSIDERED, and where this guard stands on each.
#
# CAUGHT:
#   * commented out in the file's own line-comment syntax ('#' in all three
#     languages here). This is the mode that was measured green before;
#   * commented out in a PowerShell '<# ... #>' block comment, including nested
#     ones;
#   * deleted outright (the property the old substring test already had, kept);
#   * the whole route file deleted or renamed - reported as a clear failure
#     rather than an unhandled traceback;
#   * demoted from an executed field to an inert one: the invocation must form
#     the VALUE of a key the tool runs - 'run:' in a workflow, 'entry:' or
#     'args:' in .pre-commit-config.yaml. A mention in a 'name:' or a
#     'description:' is not a wiring. That is a claim about OWNERSHIP, which is
#     structural, and it holds for every spelling: inline, block scalar, flow
#     sequence, block sequence. An earlier version asserted the lexical CONTEXT
#     instead - a proxy - and so rejected 16 of the 18 ways this workflow spells
#     'run:', plus the block-sequence 'args:' and the 'entry: |' forms that
#     pre-commit executes identically. A proxy fails in BOTH directions and each
#     direction looks like its own separate bug;
#   * buried in a here-document or a PowerShell here-string, which are data;
#   * made unreachable by an UNCONDITIONAL, TOP-LEVEL 'exit'/'return' earlier in
#     the same executed body.
#
# KNOWINGLY NOT CAUGHT. Stated because a guard whose limits are documented is
# more useful than one whose limits are discovered later:
#   * an always-false guard ('if false; then ... fi', 'if ($false) { ... }').
#     Deciding this needs evaluation, not parsing;
#   * shadowing - redefining check()/Invoke-Check as a no-op, or shadowing
#     $python with something inert. Same reason;
#   * a CONDITIONAL early exit, or one nested in any block. Only an
#     unconditional top-level one is flagged, deliberately: the conservative
#     rule has no false positives on these four files, and a false positive
#     here would push people to delete the guard;
#   * an invocation that only ever appears inside a display string, e.g.
#     echo "run python scripts/check_secrets_baseline.py". String bodies are
#     KEPT on purpose, because quoting an interpreter path is idiomatic and
#     must not be mistaken for a bypass;
#   * disabling the machinery from outside these files - 'pre-commit uninstall',
#     a branch-protection change, or a workflow-level 'if:' that skips the job.
#     Nothing in the four files' text can show that;
#   * the validator being invoked but its exit code discarded. That is asserted
#     elsewhere and is out of this slice's scope by contract.
# --------------------------------------------------------------------------

#: What each route must do with the invocation. For a whole-file script the
#: constraint is the LANGUAGE (the entire file is executed code). For YAML it is
#: OWNERSHIP - which key's value the invocation forms - because the same
#: executed position is spelled inline, as a block scalar, or as a sequence, and
#: those produce different lexical contexts. Constraining the context there
#: rejected 16 of the 18 ways this workflow spells `run:`.
ROUTE_RULES: dict[str, tuple[frozenset[str] | None, frozenset[str] | None]] = {
    ".github/workflows/ci.yml": (None, frozenset({"run"})),
    ".pre-commit-config.yaml": (None, frozenset({"entry", "args"})),
    "scripts/check.ps1": (frozenset({"powershell"}), None),
    "scripts/check.sh": (frozenset({"shell"}), None),
}

VALIDATOR = "check_secrets_baseline"


def route_text(relative_path: str) -> str:
    path = REPO_ROOT / relative_path
    assert path.is_file(), (
        f"{relative_path} does not exist. It is one of the four routes that must run the "
        "secrets baseline validator; if it moved, move this rule with it."
    )
    return path.read_text(encoding="utf-8")


def route_problems(relative_path: str, text: str) -> list[str]:
    contexts, keys = ROUTE_RULES[relative_path]
    return source_scan.invocation_problems(relative_path, text, VALIDATOR, contexts, keys)


def dense(text: str) -> int:
    """Characters that are not whitespace. Blanking preserves length, not this."""
    return sum(1 for char in text if not char.isspace())


def without_the_invocation(text: str) -> str:
    """Delete every line invoking the validator."""
    return "".join(line for line in text.splitlines(keepends=True) if VALIDATOR not in line)


def with_the_invocation_commented_out(text: str, opener: str = "# ") -> str:
    """Comment out every line invoking the validator, preserving indentation.

    '#' is the line-comment character in all three languages involved, so one
    helper covers YAML, PowerShell and POSIX shell. Every occurrence is mutated,
    so the mutation stays meaningful if a route ever gains a second invocation.
    """
    mutated = []
    for line in text.splitlines(keepends=True):
        if VALIDATOR in line:
            stripped = line.lstrip()
            mutated.append(line[: len(line) - len(stripped)] + opener + stripped)
        else:
            mutated.append(line)
    return "".join(mutated)


# --------------------------------------------------------------------------
# The guard against the guard. Borrowed from
# tests/unit/test_no_live_fetcher_in_tests.py::test_the_scan_is_not_vacuous.
#
# The failure mode being controlled for is specific: if the scanner ever fell
# back to returning a file unchanged, every assertion below would keep passing
# while checking nothing but a substring again. So prove, per file, that the
# scan really ran and really removed content.
# --------------------------------------------------------------------------


def test_the_route_scan_is_not_vacuous() -> None:
    assert len(ROUTE_RULES) == 4, "the four historical routes must all still be covered"
    for relative_path in sorted(ROUTE_RULES):
        text = route_text(relative_path)
        assert text.strip(), f"{relative_path} is empty"
        assert VALIDATOR in text, f"{relative_path} does not mention the validator at all"
        # Raises UnknownLanguage rather than degrading to substring semantics.
        lines = source_scan.scan(relative_path, text)
        executable = source_scan.executable_text(lines)
        assert dense(executable) < dense(text), (
            f"the scan removed nothing from {relative_path}. Every one of these files "
            "carries comments, so a scan that removes nothing is a scan that did not "
            "run - and this whole section would then be asserting a substring again."
        )
        assert VALIDATOR in executable


def test_an_unscanned_file_type_fails_closed() -> None:
    """A route in a language with no scanner must raise, never quietly pass."""
    with pytest.raises(source_scan.UnknownLanguage):
        source_scan.scan("scripts/check.rb", "system 'check_secrets_baseline'\n")


# --------------------------------------------------------------------------
# The three acceptance cases, per route. Untouched must PASS; deleted and
# commented-out must both FAIL.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relative_path", sorted(ROUTE_RULES))
def test_every_route_invokes_the_validator(relative_path: str) -> None:
    problems = route_problems(relative_path, route_text(relative_path))
    assert problems == [], (
        f"{relative_path} no longer runs the secrets baseline validator; "
        "the corruption this suite reproduces would ship undetected from that route. "
        f"{problems}"
    )


@pytest.mark.parametrize("relative_path", sorted(ROUTE_RULES))
def test_commenting_out_the_invocation_is_detected(relative_path: str) -> None:
    """The regression test for the substring guard.

    Measured on the tree this replaced: deleting the invocation went red in all
    four routes, and commenting it out stayed GREEN in all four. Commenting out
    is the likelier bypass of the two, so the guard was missing the mode it most
    needed to catch.
    """
    text = route_text(relative_path)
    assert route_problems(relative_path, text) == [], "positive control: untouched must pass"
    commented = with_the_invocation_commented_out(text)
    assert VALIDATOR in commented, "the mutation must keep the substring, or it proves nothing"
    assert route_problems(relative_path, commented) != []


@pytest.mark.parametrize("relative_path", sorted(ROUTE_RULES))
def test_deleting_the_invocation_is_still_detected(relative_path: str) -> None:
    """The property the substring test already had. It must not be traded away."""
    text = route_text(relative_path)
    deleted = without_the_invocation(text)
    assert VALIDATOR not in deleted
    assert route_problems(relative_path, deleted) != []


def test_a_powershell_block_comment_is_detected() -> None:
    """'#' is not the only way to comment out a PowerShell line."""
    relative_path = "scripts/check.ps1"
    text = route_text(relative_path)
    blocked = "".join(
        f"<#{line}#>\n" if VALIDATOR in line else line for line in text.splitlines(keepends=True)
    )
    assert VALIDATOR in blocked
    assert route_problems(relative_path, blocked) != []


@pytest.mark.parametrize(
    "relative_path", ["scripts/check.sh", "scripts/check.ps1", ".github/workflows/ci.yml"]
)
def test_an_unconditional_top_level_early_exit_is_detected(relative_path: str) -> None:
    """Present, uncommented, and never reached.

    The terminator takes the invocation line's own indentation, so inside the
    workflow's ``run:`` block scalar the mutation stays valid YAML and stays
    inside the same executed body - otherwise this would be measuring a parse
    failure rather than reachability.
    """
    text = route_text(relative_path)
    mutated = []
    for line in text.splitlines(keepends=True):
        if VALIDATOR in line:
            indent = line[: len(line) - len(line.lstrip())]
            mutated.append(f"{indent}exit 0\n")
        mutated.append(line)
    injected = "".join(mutated)
    assert VALIDATOR in injected
    assert route_problems(relative_path, injected) != []


def test_a_deleted_route_file_is_reported_clearly() -> None:
    """A missing route must fail with an explanation, not a traceback."""
    with pytest.raises(AssertionError, match="does not exist"):
        route_text("scripts/check.does-not-exist.sh")


# --------------------------------------------------------------------------
# False-positive controls for the scanner itself. A stripper that mangles
# correct code is a guard that fires on correct work.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative_path", "expansion"),
    [
        (".github/workflows/ci.yml", "${#files[@]}"),
        ("scripts/check.sh", "${#FAILURES[@]}"),
    ],
)
def test_parameter_expansion_is_not_mistaken_for_a_comment(
    relative_path: str, expansion: str
) -> None:
    """Both real shell routes contain a '#' that is NOT a comment.

    ``line.split("#")[0]`` truncates these lines mid-expression. That is why the
    scanner tracks quoting and word boundaries instead.
    """
    text = route_text(relative_path)
    assert expansion in text, "the probe is stale; pick an expansion the file still has"
    executable = source_scan.executable_text(source_scan.scan(relative_path, text))
    assert expansion in executable


# --------------------------------------------------------------------------
# Legitimate restructurings must NOT fire. Found by probing the guard after it
# was reported complete: the rule requiring the invocation to sit under an
# executed key was over-fitted to the FLOW spelling that happens to be in
# .pre-commit-config.yaml today, and rejected the block-sequence spelling that
# pre-commit executes identically. That is a false positive on correct work -
# the failure mode this whole suite is most anxious about, because it is the
# one that gets a guard deleted rather than fixed.
#
# A sequence item now inherits the key it sits under, which is what the YAML
# structure means rather than a special case bolted on to make one input pass.
# test_a_mention_under_a_non_executed_key_is_still_rejected is the control that
# keeps that honest: the key rule must still have teeth.
# --------------------------------------------------------------------------


def with_the_invocation_replaced_by(text: str, replacement: str) -> str:
    return "".join(
        replacement if VALIDATOR in line else line for line in text.splitlines(keepends=True)
    )


@pytest.mark.parametrize(
    ("label", "relative_path", "replacement"),
    [
        (
            "pre-commit args as a BLOCK sequence",
            ".pre-commit-config.yaml",
            "        entry: python\n        args:\n          - scripts/check_secrets_baseline.py\n",
        ),
        (
            "pre-commit args as a flow sequence",
            ".pre-commit-config.yaml",
            '        entry: python\n        args: ["scripts/check_secrets_baseline.py"]\n',
        ),
        (
            "shell invocation extracted into a function",
            "scripts/check.sh",
            'run_secrets_shape() {\n  "${PYTHON}" scripts/check_secrets_baseline.py\n}\n'
            'check "Secrets baseline shape" run_secrets_shape\n',
        ),
        (
            "PowerShell backtick line continuation",
            "scripts/check.ps1",
            'Invoke-Check "Secrets baseline shape" { & $python `\n'
            "    scripts/check_secrets_baseline.py }\n",
        ),
        (
            "PowerShell block re-indented",
            "scripts/check.ps1",
            '    Invoke-Check "Secrets baseline shape" '
            "{ & $python scripts/check_secrets_baseline.py }\n",
        ),
    ],
)
def test_legitimate_restructurings_still_pass(
    label: str, relative_path: str, replacement: str
) -> None:
    text = with_the_invocation_replaced_by(route_text(relative_path), replacement)
    problems = route_problems(relative_path, text)
    assert problems == [], f"false positive on a legitimate restructuring ({label}): {problems}"


def test_a_folded_run_scalar_is_still_a_script() -> None:
    """`run: >` executes exactly as `run: |` does."""
    relative_path = ".github/workflows/ci.yml"
    folded = route_text(relative_path).replace("run: |", "run: >")
    assert route_problems(relative_path, folded) == []


def test_an_inline_run_step_is_accepted() -> None:
    """The prevailing spelling in this very file, and it used to be rejected.

    Measured on `.github/workflows/ci.yml`: 2 of its 18 `run:` keys are block
    scalars and 16 are inline. Requiring the block form meant requiring the
    minority spelling, so collapsing a two-line step - the most ordinary edit
    imaginable - turned the guard red on correct work.
    """
    relative_path = ".github/workflows/ci.yml"
    text = route_text(relative_path)
    collapsed = text.replace(
        "        run: |\n"
        "          git fetch --no-tags --depth=1 origin "
        "+refs/heads/main:refs/remotes/origin/main\n"
        "          python scripts/check_secrets_baseline.py --require-reference\n",
        "        run: python scripts/check_secrets_baseline.py --require-reference\n",
    )
    assert collapsed != text, "the mutation did not apply; it would prove nothing"
    assert route_problems(relative_path, collapsed) == []


def test_a_precommit_entry_block_scalar_is_accepted() -> None:
    """The mirror of the inline case, and the one the design contradicted itself on.

    `entry:` used to be treated as a shell script, producing context `shell`,
    while this route's rule demanded context `yaml` - so the two halves of the
    design disagreed about one construct and rejected it. Ownership does not
    care how the value is spelled.
    """
    relative_path = ".pre-commit-config.yaml"
    text = with_the_invocation_replaced_by(
        route_text(relative_path),
        "        entry: |\n          python scripts/check_secrets_baseline.py\n",
    )
    assert route_problems(relative_path, text) == []


def test_precommit_entry_is_not_shell_interpreted() -> None:
    """A '#' in an `entry:` is a literal argument, not a comment.

    pre-commit shlex-splits `entry` and execs it; no shell sees it. Stripping
    comments there would corrupt the value, which is why only `run:` is treated
    as a shell script.
    """
    source = "repos:\n  - hooks:\n      - entry: |\n          python x.py --tag '#1'\n"
    executable = source_scan.executable_text(source_scan.scan("x.yaml", source))
    assert "--tag '#1'" in executable


@pytest.mark.parametrize(
    ("label", "relative_path", "replacement"),
    [
        (
            "inline name:",
            ".pre-commit-config.yaml",
            "        name: runs scripts/check_secrets_baseline.py\n",
        ),
        (
            "name: as a block scalar",
            ".pre-commit-config.yaml",
            "        name: |\n          runs scripts/check_secrets_baseline.py\n",
        ),
        (
            "description: prose",
            ".pre-commit-config.yaml",
            "        description: |\n          calls scripts/check_secrets_baseline.py for you\n",
        ),
        (
            "inline name: in the workflow",
            ".github/workflows/ci.yml",
            "        name: runs scripts/check_secrets_baseline.py\n",
        ),
        (
            "workflow name: as a block scalar",
            ".github/workflows/ci.yml",
            "        name: |\n          runs scripts/check_secrets_baseline.py\n",
        ),
    ],
)
def test_a_mention_under_a_non_executed_key_is_still_rejected(
    label: str, relative_path: str, replacement: str
) -> None:
    """The control that stops the key rule from becoming meaningless.

    Widening ownership to cover every spelling of an EXECUTED field must not
    slide into accepting the invocation anywhere at all. A hook or step whose
    `name:` or `description:` merely mentions the validator, with no executed
    field invoking it, is still unwired. If any of these ever goes green, the
    rule has been relaxed until it no longer says anything.
    """
    text = with_the_invocation_replaced_by(route_text(relative_path), replacement)
    assert VALIDATOR in text
    assert route_problems(relative_path, text) != [], (
        f"a mention under a non-executed key was ACCEPTED ({label}); the key rule "
        "has lost its teeth"
    )


def test_shell_comment_rules() -> None:
    lines = source_scan.scan("x.sh", 'a=1 # gone\nb="kept # inside"\nc=${#arr[@]}\n')
    executable = source_scan.executable_text(lines)
    assert "gone" not in executable
    assert "kept # inside" in executable
    assert "${#arr[@]}" in executable


def test_shell_heredoc_bodies_are_data() -> None:
    source = "cat <<'EOF'\ncheck_secrets_baseline\nEOF\nreal_command\n"
    executable = source_scan.executable_text(source_scan.scan("x.sh", source))
    assert "check_secrets_baseline" not in executable
    assert "real_command" in executable


def test_process_substitution_is_not_a_heredoc() -> None:
    """scripts/check.sh really uses `done < <(git ls-files -z)`."""
    source = "while read -r f; do :; done < <(git ls-files -z)\nkeep_me\n"
    executable = source_scan.executable_text(source_scan.scan("x.sh", source))
    assert "keep_me" in executable
    assert "git ls-files -z" in executable


def test_powershell_comment_rules() -> None:
    source = '<# block\ncheck_secrets_baseline\n#>\n$x = "kept # inside"\n$y = 1 # gone\n'
    executable = source_scan.executable_text(source_scan.scan("x.ps1", source))
    assert "check_secrets_baseline" not in executable
    assert "kept # inside" in executable
    assert "gone" not in executable


def test_powershell_here_strings_are_data() -> None:
    source = "$t = @'\ncheck_secrets_baseline\n'@\nreal_command\n"
    executable = source_scan.executable_text(source_scan.scan("x.ps1", source))
    assert "check_secrets_baseline" not in executable
    assert "real_command" in executable


def test_yaml_comment_rules() -> None:
    source = 'a: 1 # gone\nb: "kept # inside"\n'
    executable = source_scan.executable_text(source_scan.scan("x.yml", source))
    assert "gone" not in executable
    assert "kept # inside" in executable


def test_yaml_block_scalars_are_owned_by_their_key() -> None:
    """The distinction that makes the workflow route checkable at all.

    A `run:` body is a shell SCRIPT, so shell comment rules apply to it. Any
    other block scalar is literal text - nothing is stripped from it, and
    nothing is blanked either. Both belong to their key, and it is OWNERSHIP,
    not the lexical context, that decides whether the position is executed.
    Blanking non-script blocks would reach the same verdict for prose by making
    it invisible; rejecting it by key is the same answer for an honest reason.
    """
    source = (
        "steps:\n"
        "  - name: real\n"
        "    run: |\n"
        "      python scripts/check_secrets_baseline.py\n"
        "  - name: prose\n"
        "    description: |\n"
        "      python scripts/check_secrets_baseline.py\n"
    )
    lines = source_scan.scan("x.yml", source)
    by_number = {line.number: line for line in lines}
    assert by_number[4].context == "shell"
    assert by_number[4].key == "run"
    assert by_number[7].context == "literal"
    assert by_number[7].key == "description"
    # Both survive stripping; only ownership separates them.
    assert "check_secrets_baseline" in by_number[7].executable
    assert (
        source_scan.invocation_problems(
            "x.yml", source, "check_secrets_baseline", None, frozenset({"run"})
        )
        == []
    )
    assert (
        source_scan.invocation_problems(
            "x.yml",
            source.replace("    run: |\n      python scripts/check_secrets_baseline.py\n", ""),
            "check_secrets_baseline",
            None,
            frozenset({"run"}),
        )
        != []
    )


def test_a_commented_line_inside_a_run_block_is_stripped_as_shell() -> None:
    source = "steps:\n  - run: |\n      # python scripts/check_secrets_baseline.py\n      true\n"
    executable = source_scan.executable_text(source_scan.scan("x.yml", source))
    assert "check_secrets_baseline" not in executable
    assert "true" in executable


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
