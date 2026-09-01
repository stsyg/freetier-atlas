"""A permanent test must never read a PER-SLICE ``agent-state/`` artefact for its
CONTENTS.

This guards a defect class the orchestrator inflicted on itself and that PR #106
removed one instance of. ``agent-state/current_contract.json`` describes the slice
CURRENTLY IN FLIGHT and every builder is instructed to overwrite it wholesale;
``agent-state/evaluation.json`` and the files under ``agent-state/evaluations/``
are the same shape (one slice's Level-2 record, replaced next slice);
``agent-state/progress.md`` is append-only, so a read of it is safe against
deletion but NOT against a reader that depends on POSITION or on the LAST entry.
A PERMANENT test that reads any of these for a value is coupled to transient
state: it changes meaning, or breaks, on an unrelated slice, and the failure
surfaces as a test error with no product change to explain it.

``agent-state/feature_list.json`` is deliberately EXEMPT. It is durable by design
and protected from wholesale rewrite (normal agents may only flip ``passes`` /
``last_verified_at`` / ``verification_evidence``), so a guard MAY legitimately
read it -- ``tests/test_repo_baseline.py`` does, and is correct to.

**The discriminator, applied per hit.** Reading a file for its CONTENTS is the
defect; asserting it EXISTS and PARSES is durable and correct. ``read_text`` used
only to feed ``json.loads`` for a *validity* check, with no key or value extracted,
is the existence/parse form and is permitted -- but only for the artefacts named
above it would still be a content read, so this guard forbids the read outright
for the per-slice files and relies on ``test_repo_baseline.py``'s existence checks
(``is_file`` / ``stat`` / a bare ``json.loads`` with no extraction) staying on the
allowed side of the line. The point is not to ban the *filename* -- prose that
merely mentions it is fine -- but to ban a permanent test *reading the bytes*.

**Why AST and not grep.** A name-grep answers "who mentions this string", not
"what reads this file"; the census that produced this guard found the per-slice
names cited in docstrings for provenance far more often than read from disk. Only
an ``ast.Call`` to a read primitive whose target path names a per-slice artefact
counts here.

**Why the file list is derived, not hand-written.** Two hand-maintained lists in
one file are edited in one breath and drift together. The per-slice set is
enumerated from ``agent-state/`` at test time (every top-level ``*.json`` except
the exempt ``feature_list.json``, plus ``progress.md`` and everything under
``evaluations/``), so a NEW per-slice artefact is covered the moment it lands
without anyone updating this test.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
AGENT_STATE = REPO_ROOT / "agent-state"

#: Durable by design and protected from wholesale rewrite -- a permanent guard
#: may legitimately read it, so it is NOT a per-slice artefact.
DURABLE_ARTEFACTS = frozenset({"feature_list.json"})

#: Read primitives. A path expression with one of these called on it, or an
#: ``open`` / ``json.load`` / ``json.loads`` applied to it, is reading CONTENTS.
_PATH_READERS = frozenset({"read_text", "read_bytes", "open", "read"})
_JSON_LOADERS = frozenset({"load", "loads"})


def _per_slice_relpaths() -> frozenset[str]:
    """Every per-slice ``agent-state/`` artefact, derived from the tree.

    Top-level ``agent-state/*.json`` except the exempt durable files, plus
    ``progress.md`` and every file under ``agent-state/evaluations/``. Returned as
    forward-slashed paths relative to the repo root so a substring test against
    ``ast.unparse`` output matches how the paths appear in source.
    """

    rels: set[str] = set()
    for path in AGENT_STATE.glob("*.json"):
        if path.name in DURABLE_ARTEFACTS:
            continue
        rels.add(path.relative_to(REPO_ROOT).as_posix())
    progress = AGENT_STATE / "progress.md"
    if progress.is_file():
        rels.add(progress.relative_to(REPO_ROOT).as_posix())
    evaluations = AGENT_STATE / "evaluations"
    if evaluations.is_dir():
        for path in evaluations.rglob("*"):
            if path.is_file():
                rels.add(path.relative_to(REPO_ROOT).as_posix())
    return frozenset(rels)


def _test_files() -> Iterator[Path]:
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        yield path


def _names_a_per_slice_artefact(source: str, per_slice: frozenset[str]) -> bool:
    """True if ``source`` (unparsed AST) references a per-slice artefact path.

    Matches the full relative path (``agent-state/current_contract.json``) so a
    bare mention of ``agent-state`` as a directory, or of the exempt
    ``feature_list.json``, does not trip. A per-slice basename alone
    (``current_contract.json``) also counts, since a test may build the path from
    a ``REPO_ROOT / "agent-state"`` prefix joined to the basename.
    """

    for rel in per_slice:
        if rel in source:
            return True
        basename = rel.rsplit("/", 1)[-1]
        # Only basenames unique to a per-slice file; ``progress.md`` and the
        # contract/evaluation names are, and evaluation records live only under
        # the evaluations/ directory so their basenames are safe too.
        if basename in source and "agent-state" in source:
            return True
    return False


def _content_reads(path: Path, per_slice: frozenset[str]) -> list[tuple[int, str]]:
    """Line numbers + source where ``path`` READS a per-slice artefact's bytes."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # <path-expr>.read_text() / .read_bytes() / .open() / .read()
        if isinstance(func, ast.Attribute) and func.attr in _PATH_READERS:
            receiver = ast.unparse(func.value)
            if _names_a_per_slice_artefact(receiver, per_slice):
                offenders.append((node.lineno, ast.unparse(node)))
                continue

        # open(<path-expr>, ...) as a bare call
        if isinstance(func, ast.Name) and func.id == "open" and node.args:
            if _names_a_per_slice_artefact(ast.unparse(node.args[0]), per_slice):
                offenders.append((node.lineno, ast.unparse(node)))
                continue

        # json.load(open(<path-expr>)) / json.loads(<...>.read_text())
        if isinstance(func, ast.Attribute) and func.attr in _JSON_LOADERS:
            if node.args and _names_a_per_slice_artefact(ast.unparse(node.args[0]), per_slice):
                offenders.append((node.lineno, ast.unparse(node)))
                continue

    return offenders


def test_no_permanent_test_reads_a_per_slice_agent_state_file() -> None:
    per_slice = _per_slice_relpaths()

    # The derivation must actually have found the known per-slice artefacts, or a
    # broken glob would make this guard pass vacuously against an empty set.
    assert "agent-state/current_contract.json" in per_slice
    assert "agent-state/evaluation.json" in per_slice
    assert "agent-state/progress.md" in per_slice
    assert "agent-state/feature_list.json" not in per_slice

    offenders: list[str] = []
    for path in _test_files():
        for lineno, snippet in _content_reads(path, per_slice):
            rel = path.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{rel}:{lineno}: {snippet}")

    assert not offenders, (
        "A permanent test reads a PER-SLICE agent-state artefact for its CONTENTS:\n  "
        + "\n  ".join(offenders)
        + "\n\nThese files are overwritten (or, for progress.md, appended to) every "
        "slice, so a guard that reads them is coupled to transient state. Anchor the "
        "guard on the SOURCE it is really about (e.g. via AST), not on a per-slice "
        "file. agent-state/feature_list.json is durable and exempt; read that if you "
        "need a stable agent-state oracle."
    )
