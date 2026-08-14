#!/usr/bin/env python3
"""Fail the build when ``.secrets.baseline`` is malformed or has lost ground.

Why this exists
---------------
``.secrets.baseline`` has been corrupted four times by four different code paths,
twice after the failure was written down at high confidence and read by the person
who then hit it. Prose has demonstrably failed, so this is a check that fails the
build instead.

The generalised root cause, measured from ``detect_secrets`` 1.5.0 rather than
recalled: ``detect_secrets.util.path.convert_local_os_path`` rewrites ``/`` to
``os.sep`` when a baseline is LOADED and when a file is SCANNED, but
``SecretsCollection.json()`` serialises those internal keys back out with **no
reverse conversion**. The asymmetry is one-way, so *every* write path on Windows
emits ``tests\\fixtures\\...`` where the committed file holds
``tests/fixtures/...``. This is not one bad script; it is the library's contract.

The two observed corruptions, and why each check below is load-bearing:

* **Mode B, backslash rewrite.** Measured: one shifted line number makes the
  ``detect-secrets`` pre-commit hook exit 3 and rewrite all 21 file keys with
  backslashes, and ``detect-secrets scan --baseline`` does the same silently at
  exit 0. Crucially the entry COUNTS are untouched, so a count-based guard is
  structurally blind to it. Only :func:`posix_key_problems` sees mode B.
* **Mode A, silent wipe.** Entries vanish instead of updating, because a refresh
  keyed by native path does not match existing posix keys. Only
  :func:`direction_problems` sees mode A.

A fifth failure this design exists to avoid: a previous guard asserted that the
right entries had *changed* and PASSED on a wipe, because a deletion is a change.
It constrained scope but not DIRECTION. Every directional check here is therefore
one-sided on purpose - counts may grow, never shrink; files may be added, never
disappear.

What fails the build, and what only informs
-------------------------------------------
FAILS: a non-posix result key; a per-file entry count that decreased; a tracked
file that disappeared entirely; a ``hashed_secret`` that is not a 40-hex SHA-1; an
entry whose ``filename`` disagrees with its result key; a result key naming a file
that no longer exists.

INFORMS ONLY: ``generated_at``, ``version``, and ``filters_used`` drift. See
:func:`advisory_notes` for the argued reasoning on ``generated_at``.

Exit codes: 0 when the baseline is sound, 1 when it is not.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE_NAME = ".secrets.baseline"

# detect-secrets writes SHA-1 hex digests. For this repository's fixture entries
# these are SHA-1 of an already-published SHA-256 content digest - digests of
# digests, never credential material - so only the SHAPE is ever inspected.
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

Results = dict[str, list[dict[str, Any]]]


def load_results(baseline: dict[str, Any], origin: str) -> Results:
    """Return the ``results`` mapping, or raise ValueError naming ``origin``."""
    results = baseline.get("results")
    if not isinstance(results, dict):
        raise ValueError(f"{origin}: 'results' is missing or is not an object")
    return results


def to_posix(key: str) -> str:
    """Normalise a result key for COMPARISON only, never for output.

    Used so the directional checks compare file identity even when one side has
    already been corrupted into backslashes. The corruption itself is reported
    separately by :func:`posix_key_problems`; normalising here would otherwise
    let mode B hide mode A.
    """
    return key.replace("\\", "/")


def posix_key_problems(results: Results) -> list[str]:
    """Every result key must be a relative posix path. This is the mode-B check."""
    problems: list[str] = []
    for key in sorted(results):
        if not key:
            problems.append("a result key is the empty string")
            continue
        if "\\" in key:
            problems.append(
                f"result key is not posix (contains a backslash): {key!r}. "
                "A Windows detect-secrets write produced this; refresh with "
                "scripts/refresh_secrets_baseline.py instead."
            )
            continue
        if key.startswith("/") or DRIVE_LETTER_RE.match(key):
            problems.append(f"result key is an absolute path, not repository-relative: {key!r}")
            continue
        if key.startswith("./"):
            problems.append(f"result key is './'-prefixed rather than plain relative: {key!r}")
            continue
        if ".." in key.split("/"):
            problems.append(f"result key escapes the repository with a '..' segment: {key!r}")
    return problems


def entry_problems(results: Results) -> list[str]:
    """Every entry must carry a well-formed SHA-1 and agree with its result key."""
    problems: list[str] = []
    for key in sorted(results):
        entries = results[key]
        if not isinstance(entries, list) or not entries:
            problems.append(f"{key}: entry list is missing, not a list, or empty")
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                problems.append(f"{key}[{index}]: entry is not an object")
                continue
            digest = entry.get("hashed_secret")
            if not isinstance(digest, str) or not SHA1_RE.match(digest):
                problems.append(
                    f"{key}[{index}]: hashed_secret is not a 40-character lowercase "
                    f"hex SHA-1: {digest!r}"
                )
            filename = entry.get("filename")
            if filename is not None and filename != key:
                problems.append(
                    f"{key}[{index}]: entry filename {filename!r} disagrees with its "
                    f"result key {key!r}"
                )
    return problems


def existence_problems(results: Results, root: pathlib.Path) -> list[str]:
    """A result key naming a file that no longer exists is a stale entry."""
    problems: list[str] = []
    for key in sorted(results):
        # Only meaningful for keys that are already well formed; a backslash key
        # is reported by posix_key_problems and would fail here for the wrong
        # reason on a posix filesystem.
        if "\\" in key or key.startswith("/") or DRIVE_LETTER_RE.match(key):
            continue
        if not (root / key).is_file():
            problems.append(
                f"{key}: result key names a file that does not exist in the working tree. "
                "If the file was deleted, remove its baseline entry in the same commit."
            )
    return problems


def direction_problems(candidate: Results, reference: Results) -> list[str]:
    """Counts may grow, never shrink. Files may be added, never disappear.

    This is the mode-A check, and it is deliberately one-sided. A guard that
    asserted only that the right entries *changed* passed on a wipe, because a
    deletion is a change.
    """
    problems: list[str] = []
    candidate_counts = {to_posix(k): len(v) for k, v in candidate.items()}
    reference_counts = {to_posix(k): len(v) for k, v in reference.items()}

    for key in sorted(reference_counts):
        before = reference_counts[key]
        if key not in candidate_counts:
            problems.append(
                f"{key}: tracked file DISAPPEARED from the baseline "
                f"({before} entr{'y' if before == 1 else 'ies'} lost). "
                "Either the file was deleted without removing its entry deliberately, "
                "or a refresh keyed by native path failed to match the posix key and "
                "dropped it instead of updating it."
            )
            continue
        after = candidate_counts[key]
        if after < before:
            problems.append(
                f"{key}: entry count DECREASED from {before} to {after}. "
                "Entries are only ever removed deliberately, in a reviewed commit."
            )
    return problems


def advisory_notes(candidate: dict[str, Any], reference: dict[str, Any] | None) -> list[str]:
    """Report drift that is worth seeing but must never fail the build.

    ``generated_at``: IGNORED for pass/fail, REPORTED for diagnosis. Failing on
    churn would fire on the very operation this machinery exists to make safe - a
    legitimate refresh necessarily updates it - and a check that fires on correct
    work teaches people to bypass it, which is worse than no check. Failing on the
    ABSENCE of churn is equally wrong, because this repository deliberately
    hand-splices single entries and preserves the timestamp when it does. Both
    regeneration and splicing are sanctioned practice here, so gating either way
    would outlaw a workflow the maintainers use on purpose. Its value is telling a
    reviewer WHICH of the two produced the diff, and that value is fully realised
    by printing it.
    """
    notes: list[str] = []
    generated_at = candidate.get("generated_at")
    if generated_at is None:
        notes.append("generated_at is absent (the file was hand-constructed rather than generated)")
    if reference is None:
        return notes

    if generated_at != reference.get("generated_at"):
        notes.append(
            f"generated_at changed {reference.get('generated_at')!r} -> {generated_at!r} "
            "(regenerated rather than spliced)"
        )
    else:
        notes.append(
            f"generated_at unchanged at {generated_at!r} (spliced rather than regenerated)"
        )

    if candidate.get("version") != reference.get("version"):
        notes.append(
            f"detect-secrets version changed {reference.get('version')!r} -> "
            f"{candidate.get('version')!r}"
        )

    before = len(reference.get("filters_used") or [])
    after = len(candidate.get("filters_used") or [])
    if before != after:
        notes.append(
            f"filters_used count changed {before} -> {after}. "
            "'detect-secrets scan --baseline' injects an is_baseline_file filter; "
            "that is harmless, but an unexplained change is worth a look."
        )
    return notes


def git_show(rev: str, path: str, root: pathlib.Path) -> str | None:
    """Return the blob at ``rev:path``, or None when the revision is unavailable."""
    completed = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=root,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8")


def is_self_reference(rev: str) -> bool:
    """True when ``rev`` names the commit being validated rather than a 'before'.

    ``HEAD`` is the commit under validation. Once a corruption is COMMITTED - which
    is exactly the case CI exists to catch - ``HEAD:.secrets.baseline`` is the
    corrupted file itself, so the directional checks compare it against itself and
    can never fail. Measured: with three entries genuinely deleted and committed,
    a HEAD-referenced run reported "passed... nothing lost".

    Only the literal is rejected, never a resolved SHA that merely coincides with
    HEAD. On a push to ``main`` the merge base legitimately IS the pushed commit,
    and failing there would break the default branch for a healthy no-op.
    """
    return rev.strip() in {"HEAD", "@"}


def resolve_reference(
    explicit: str | None,
    root: pathlib.Path,
    require_reference: bool = False,
) -> tuple[str, str] | None:
    """Return (revision, baseline text) for the 'before' side, or None.

    Resolution order, most specific first. The fork point is preferred over a
    branch tip so that a branch which is merely BEHIND main is not accused of
    deleting entries that main gained after the branch was cut.

    ``HEAD`` is a last-resort fallback for local runs with uncommitted changes,
    and is EXCLUDED under ``require_reference``. Including it there made the flag
    vacuous: ``git show HEAD:.secrets.baseline`` nearly always succeeds, so a
    reference was always "resolved", the flag never fired, and the directional
    check silently degraded into a self-comparison that self-certifies. A flag
    that reports success because it compared a file to itself is worse than no
    flag, so it now fails closed.
    """
    candidates: list[str] = []
    if explicit:
        if require_reference and is_self_reference(explicit):
            return None
        candidates.append(explicit)
    else:
        from_env = os.environ.get("SECRETS_BASELINE_REF")
        if from_env and not (require_reference and is_self_reference(from_env)):
            candidates.append(from_env)
        merge_base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=root,
            capture_output=True,
        )
        if merge_base.returncode == 0:
            candidates.append(merge_base.stdout.decode().strip())
        candidates.append("origin/main")
        if not require_reference:
            candidates.append("HEAD")

    for rev in candidates:
        if not rev:
            continue
        text = git_show(rev, BASELINE_NAME, root)
        if text is not None:
            return rev, text
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--baseline",
        default=None,
        help=f"path to the baseline to validate (default: {BASELINE_NAME} at the repository root)",
    )
    parser.add_argument(
        "--reference",
        default=None,
        help=(
            "git revision holding the 'before' baseline for the directional checks "
            "(default: merge-base with origin/main, then origin/main, then HEAD)"
        ),
    )
    parser.add_argument(
        "--require-reference",
        action="store_true",
        help=(
            "fail rather than skip when no reference baseline can be resolved (use in CI). "
            "HEAD is excluded under this flag, because it is the commit being validated"
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="repository root used to resolve result keys (default: this script's parent)",
    )
    args = parser.parse_args(argv)

    root = pathlib.Path(args.repo_root).resolve() if args.repo_root else REPO_ROOT
    baseline_path = pathlib.Path(args.baseline) if args.baseline else root / BASELINE_NAME

    if not baseline_path.is_file():
        print(f"secrets baseline check FAILED: {baseline_path} does not exist", file=sys.stderr)
        return 1

    try:
        candidate = json.loads(baseline_path.read_text(encoding="utf-8"))
        candidate_results = load_results(candidate, str(baseline_path))
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"secrets baseline check FAILED: {exc}", file=sys.stderr)
        return 1

    problems = posix_key_problems(candidate_results)
    problems += entry_problems(candidate_results)
    problems += existence_problems(candidate_results, root)

    baseline_text = baseline_path.read_text(encoding="utf-8")
    resolved = resolve_reference(args.reference, root, args.require_reference)
    reference: dict[str, Any] | None = None
    if resolved is None:
        message = (
            "no reference baseline could be resolved, so the directional checks "
            "(counts non-decreasing, files never disappearing) DID NOT RUN. "
            "HEAD does not count: it is the commit under validation, so comparing "
            "against it would compare the file with itself"
        )
        if args.require_reference:
            problems.append(message)
        else:
            print(f"  SKIPPED: {message}")
    else:
        rev, text = resolved
        # Defence in depth. resolve_reference already refuses to hand back HEAD
        # under --require-reference, so this only fires on a permissive local run
        # - or on a future edit that reintroduces the fallback.
        if is_self_reference(rev) and text == baseline_text:
            message = (
                f"the only reference available was {rev}, and the file under validation "
                "is byte-identical to it, so the directional checks would compare it "
                "against ITSELF and could never fail"
            )
            if args.require_reference:
                problems.append(message)
            else:
                print(f"  SKIPPED: {message}")
        else:
            try:
                reference = json.loads(text)
                reference_results = load_results(reference, f"{rev}:{BASELINE_NAME}")
            except ValueError as exc:
                problems.append(f"reference baseline at {rev} is unusable: {exc}")
            else:
                print(f"  reference: {rev}")
                problems += direction_problems(candidate_results, reference_results)

    for note in advisory_notes(candidate, reference):
        print(f"  note: {note}")

    if problems:
        print(
            f"secrets baseline check FAILED: {len(problems)} problem(s) in {baseline_path.name}.",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nDo NOT hand-repair by re-running detect-secrets on Windows; that is what "
            "produced this. Restore the file from git and use "
            "scripts/refresh_secrets_baseline.py, which normalises keys to posix and "
            "refuses to write a baseline that would fail this check.",
            file=sys.stderr,
        )
        return 1

    files = len(candidate_results)
    entries = sum(len(v) for v in candidate_results.values())
    print(
        f"secrets baseline check passed: {files} tracked file(s), {entries} entr(y/ies), "
        "all keys posix, all digests well-formed, nothing lost."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
