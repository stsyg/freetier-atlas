"""Extract the EXECUTABLE content of a source file, in that file's own language.

Why this module exists
----------------------
A guard that asserts a build step is still WIRED must constrain EFFECT, not
merely PRESENCE. ``"check_secrets_baseline" in text`` constrains presence: it
goes red when the invocation is DELETED and stays green when the invocation is
COMMENTED OUT, which is the likelier bypass, because commenting out is what a
contributor does to a check they believe is misfiring. That is the same defect
shape this repository has now found three times: a guard that pinned that
entries *changed* without pinning *how*, a fallback chain ending in a literal
that always resolved, and this one.

The fix is not a regex that happens to reject a leading ``#``. It is to ask
what the guard is trying to guarantee - *this file really runs the validator* -
and to answer it by removing the parts of the file that do not execute, in the
file's own syntax, before looking for the invocation at all.

``tests/unit/test_no_live_fetcher_in_tests.py`` reaches the same conclusion for
Python and reaches for ``ast``. Three of the four routes here are YAML,
PowerShell and POSIX shell, so an AST is not available; what carries over is the
reasoning, not the tool.

What each scanner classifies
----------------------------
Every character is sorted into one of four kinds, and two derived texts are
produced from that classification:

``executable``
    Comments and inert literal bodies (here-documents, PowerShell here-strings,
    non-script YAML block scalars) are blanked. Quoted string bodies are KEPT,
    deliberately: quoting an interpreter path is idiomatic and must not be
    mistaken for a bypass.
``code``
    As above, and quoted string bodies are blanked too. Used only for structural
    reasoning - brace and keyword depth - where a brace inside a string would
    corrupt the count.

Blanking preserves length and newlines, so line numbers and columns still line
up with the original file.

Naivety this deliberately avoids
--------------------------------
``line.split("#")[0]`` would corrupt real code in two of these four files:
``scripts/check.sh`` contains ``"${#FAILURES[@]}"`` and ``.github/workflows/ci.yml``
contains ``"${#files[@]}"``. A stripper that mangles correct code produces a
false positive, and a guard that fires on correct work teaches people to delete
it - which leaves the tree worse off than the gap being closed.

Failing closed
--------------
:func:`scan` raises :class:`UnknownLanguage` for a suffix it has no scanner for.
It must never fall back to returning the text unchanged: that fallback would
silently restore the substring semantics this module exists to replace, and
every caller would keep passing while checking nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import yaml

__all__ = [
    "ExecutedValue",
    "SourceLine",
    "UnknownLanguage",
    "all_values",
    "code_text",
    "executable_text",
    "executed_values",
    "invocation_problems",
    "scan",
]


class UnknownLanguage(RuntimeError):
    """No scanner is registered for this file. Raised rather than falling back."""


@dataclass(frozen=True)
class SourceLine:
    """One line of a scanned file.

    Produced only for whole-file scripts (``.sh``, ``.ps1``). YAML structure is
    resolved by a parser instead, so there is no per-line key here: asking which
    key owns a LINE was the question that kept being answered wrongly.

    ``context`` is the language whose rules produced this line's content.
    ``region`` separates independently executed bodies, so an ``exit`` in one
    says nothing about reachability in another.
    """

    number: int
    raw: str
    executable: str
    code: str
    context: str | None
    region: int


# --------------------------------------------------------------------------
# Character-level helpers.
# --------------------------------------------------------------------------


def _blank(text: str, start: int, stop: int, *targets: list[str]) -> None:
    """Replace a span with spaces in each target, preserving newlines."""
    for index in range(max(start, 0), min(stop, len(text))):
        if text[index] != "\n":
            for target in targets:
                target[index] = " "


def _line_spans(text: str) -> list[tuple[int, int]]:
    """(start, stop) offsets of each line, stop excluding the newline."""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == "\n":
            spans.append((start, index))
            start = index + 1
    if start <= len(text) - 1 or not spans:
        spans.append((start, len(text)))
    return spans


# --------------------------------------------------------------------------
# POSIX shell.
# --------------------------------------------------------------------------

# In shell, '#' opens a comment only at the start of a word. This is exactly why
# "${#FAILURES[@]}" survives: the '#' there follows '{', which is not a word
# boundary, and in both real files it is inside double quotes as well.
_SH_COMMENT_AFTER = " \t\n;&|("

_SH_HEREDOC = re.compile(r"<<(-?)[ \t]*(?:(['\"])([A-Za-z_][\w.-]*)\2|([A-Za-z_][\w.-]*))")


def _scan_shell(text: str, executable: list[str], code: list[str], start: int, stop: int) -> None:
    index = start
    pending: list[str] = []
    while index < stop:
        char = text[index]

        if char == "\\" and index + 1 < stop and text[index + 1] != "\n":
            index += 2
            continue

        if char == "'":
            end = text.find("'", index + 1, stop)
            end = stop if end == -1 else end
            _blank(text, index + 1, end, code)
            index = end + 1
            continue

        if char == '"':
            cursor = index + 1
            while cursor < stop:
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == '"':
                    break
                cursor += 1
            _blank(text, index + 1, min(cursor, stop), code)
            index = min(cursor, stop) + 1
            continue

        if char == "#" and (index == start or text[index - 1] in _SH_COMMENT_AFTER):
            end = text.find("\n", index, stop)
            end = stop if end == -1 else end
            _blank(text, index, end, executable, code)
            index = end
            continue

        # '<<' opens a here-document; '<<<' is a here-string and '< <(' is
        # process substitution, which scripts/check.sh genuinely uses.
        if text.startswith("<<", index) and not text.startswith("<<<", index):
            match = _SH_HEREDOC.match(text, index)
            if match:
                pending.append(match.group(3) or match.group(4))
                index = match.end()
                continue

        if char == "\n":
            index += 1
            while pending:
                delimiter = pending.pop(0)
                while index < stop:
                    end = text.find("\n", index, stop)
                    end = stop if end == -1 else end
                    closing = text[index:end].strip() == delimiter
                    _blank(text, index, end, executable, code)
                    index = min(end + 1, stop)
                    if closing:
                        break
            continue

        index += 1


# --------------------------------------------------------------------------
# PowerShell.
# --------------------------------------------------------------------------

_PS_COMMENT_AFTER = " \t\n;{}()|,=&"


def _scan_powershell(text: str, executable: list[str], code: list[str]) -> None:
    index = 0
    length = len(text)
    while index < length:
        # Block comments nest in PowerShell, so this counts rather than
        # searching for the first '#>'.
        if text.startswith("<#", index):
            depth = 1
            cursor = index + 2
            while cursor < length and depth:
                if text.startswith("<#", cursor):
                    depth += 1
                    cursor += 2
                    continue
                if text.startswith("#>", cursor):
                    depth -= 1
                    cursor += 2
                    continue
                cursor += 1
            _blank(text, index, cursor, executable, code)
            index = cursor
            continue

        if text.startswith('@"', index) or text.startswith("@'", index):
            quote = text[index + 1]
            line_end = text.find("\n", index)
            if line_end != -1 and text[index + 2 : line_end].strip() == "":
                terminator = quote + "@"
                cursor = line_end + 1
                while cursor < length:
                    end = text.find("\n", cursor)
                    end = length if end == -1 else end
                    if text[cursor:end].strip().startswith(terminator):
                        cursor = end
                        break
                    cursor = end + 1 if end < length else length
                _blank(text, index, cursor, executable, code)
                index = cursor
                continue

        char = text[index]

        if char == "'":
            cursor = index + 1
            while cursor < length:
                if text[cursor] == "'":
                    if cursor + 1 < length and text[cursor + 1] == "'":
                        cursor += 2
                        continue
                    break
                cursor += 1
            _blank(text, index + 1, min(cursor, length), code)
            index = min(cursor, length) + 1
            continue

        if char == '"':
            cursor = index + 1
            while cursor < length:
                if text[cursor] == "`":
                    cursor += 2
                    continue
                if text[cursor] == '"':
                    break
                cursor += 1
            _blank(text, index + 1, min(cursor, length), code)
            index = min(cursor, length) + 1
            continue

        if char == "#" and (index == 0 or text[index - 1] in _PS_COMMENT_AFTER):
            end = text.find("\n", index)
            end = length if end == -1 else end
            _blank(text, index, end, executable, code)
            index = end
            continue

        index += 1


# --------------------------------------------------------------------------
# YAML structure, resolved by a real parser, and selected by WALKING the schema.
#
# This module tried three times to answer a STRUCTURAL question about YAML with
# hand-rolled line matching, and lost a round to each new spelling: the lexical
# context, then the innermost key, and next it would have been the key's start
# column. Each rework had the same shape, which is the signature of a partial
# parser standing in for a real one. Ten distinct defects were measured across
# those revisions - `with: run:`, `env: run:` and a duplicate `entry:` accepted
# though none of them executes; flow mappings, CRLF files, three alias shapes
# and a `<<:` merge rejected though all are legitimate; and unparseable files
# certified as correctly wired. Closing those by hand means implementing flow
# syntax, anchors, aliases, merge keys, duplicate-key resolution and CRLF - a
# YAML parser, written here, badly.
#
# TWO decisions are load-bearing, and neither is about the dependency.
#
# WALK, DO NOT SEARCH. Selection consumes the pattern one segment at a time, so
# a step's `with:` and `env:` are simply never visited. Ancestry is enforced by
# CONSTRUCTION, not by an exclusion list: nothing here names `with`, and a rule
# spelled "not under `with`" would be fitted to the decoy in front of it. A
# recursive hunt for any key called `run` would reproduce the nearest-key defect
# with a parser underneath - harder to see, not easier.
#
# `safe_load`, NOT `compose`. The composer keeps line marks, but it PRESERVES
# duplicate keys and does not expand `<<` merge keys. `safe_load` resolves
# last-wins and expands merges, which is literally what pre-commit and Actions
# execute. Measured: with `entry:` written twice, the first naming the validator
# and the second overriding it, a compose-based rule ACCEPTS while the file runs
# something else. A verdict computed from anything but the executed semantics is
# a proxy for the truth, which is the mistake this slice exists to correct. The
# cost is line numbers, paid deliberately: a wrong line number is an annoyance,
# a wrong verdict is the failure. Diagnostics name the key PATH instead, which
# says WHY a position is not executed rather than only where it sits.
#
# No new dependency: pyyaml==6.0.3 is a declared `[project] dependencies` pin in
# pyproject.toml, kept in step with apps/api/requirements.txt by
# tests/unit/test_requirements_sync.py, and CI installs it via
# `pip install -e ".[dev]"`.
#
# What the parser still has to be TOLD to do: fail closed. `yaml.YAMLError` is
# caught explicitly and turned into a rejection. Without that the guard raises
# instead of rejecting, and a route file too broken to parse was previously
# certified as correctly wired.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutedValue:
    """A scalar the tool actually runs, and the schema path it sits at."""

    path: tuple[str, ...]
    value: str
    shell: bool


def _select(
    node: object, pattern: tuple[str, ...], path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield (path, scalar) for every node at ``pattern``, respecting SCHEMA TYPE.

    Every segment declares the type of the value it names. A bare segment names
    a value that is a mapping to descend into, or - when it is last - a string.
    A ``[]`` suffix names a value that must be a SEQUENCE, whose items are
    descended into, or - when it is last - are the strings themselves.

    That typing is the whole correction. An earlier version traversed any list
    transparently and descended any mapping, so it accepted seven distinct
    schema-INVALID shapes: ``run:`` and ``entry:`` written as sequences,
    ``args:`` as a scalar or as a list of lists, ``steps:`` and ``hooks:`` as
    mappings, and ``jobs:`` as a list. None of those loads in the tool that
    reads it, so certifying them as wired is the same defect as certifying an
    unparseable file. Fixing them one at a time is what produced the earlier
    narrow version; typing the segments states the rule once.

    ``*`` matches exactly one mapping key, which is how an arbitrary job id is
    named. Recursion is bounded by the pattern length because every step
    consumes a segment, so a cyclic document cannot loop here.
    """
    if not pattern:
        return
    head, rest = pattern[0], pattern[1:]
    listed = head.endswith("[]")
    name = head[:-2] if listed else head

    if name == "*":
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            yield from _consume(value, listed, rest, (*path, str(key)))
        return

    if not isinstance(node, dict) or name not in node:
        return
    yield from _consume(node[name], listed, rest, (*path, name))


def _consume(
    value: object, listed: bool, rest: tuple[str, ...], path: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Apply a segment's declared type to the value it named."""
    if listed:
        if not isinstance(value, list):
            return
        for index, item in enumerate(value):
            yield from _descend(item, rest, (*path, f"[{index}]"))
        return
    yield from _descend(value, rest, path)


def _descend(
    value: object, rest: tuple[str, ...], path: tuple[str, ...]
) -> Iterator[tuple[tuple[str, ...], str]]:
    if rest:
        if isinstance(value, dict):
            yield from _select(value, rest, path)
        return
    if isinstance(value, str):
        yield path, value


def _single_document(text: str) -> object:
    """The ONE document a tool reads.

    pre-commit and GitHub Actions each read a single document; neither iterates
    a multi-document stream. Loading them all accepted a needle in the second
    document of a file the tool would never look past the first of. ``safe_load``
    raises on a multi-document stream, so this is the correct semantics rather
    than a guard bolted on afterwards.
    """
    return yaml.safe_load(text)


def executed_values(
    text: str, patterns: tuple[tuple[tuple[str, ...], bool], ...]
) -> list[ExecutedValue]:
    """Every scalar the tool executes, found by walking typed ``patterns``.

    Each pattern is ``(typed path, is_shell_script)``. ``is_shell_script`` says
    whether a '#' inside the value is a comment: GitHub Actions' ``run:`` is a
    shell script, while pre-commit's ``entry`` is shlex-split and exec'd with no
    shell involved, so a '#' there is a literal argument.

    Propagates ``yaml.YAMLError`` so callers fail closed rather than treating an
    unparseable file as one that happens to contain nothing.
    """
    document = _single_document(text)
    if document is None:
        return []
    found: list[ExecutedValue] = []
    for pattern, shell in patterns:
        for path, value in _select(document, pattern):
            found.append(ExecutedValue(path, value, shell))
    return found


def all_values(text: str) -> list[tuple[tuple[str, ...], str]]:
    """Every scalar and its path, so a rejection can say where the mention IS.

    Diagnostics only - never the verdict. Carries a cycle guard because a
    recursive alias makes the loaded structure self-referencing, and this walk
    runs ONLY while building a rejection message: without the guard the check
    crashes precisely when it is trying to explain a failure.
    """
    found: list[tuple[tuple[str, ...], str]] = []
    seen: set[int] = set()

    def walk(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, (dict, list)):
            if id(node) in seen:
                return
            seen.add(id(node))
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, (*path, str(key)))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, (*path, f"[{index}]"))
        elif isinstance(node, str):
            found.append((path, node))

    document = _single_document(text)
    if document is not None:
        walk(document, ())
    return found


def survives_in_the_composed_document(text: str, needle: str) -> bool | None:
    """Is ``needle`` a value the DOCUMENT contains but the loaded mapping drops?

    Diagnostics only. The composer preserves duplicate keys, so a value present
    here but absent from ``safe_load``'s result was written in the file and then
    overridden by a later duplicate - which is a different thing from being
    commented out, and the two must not be reported as each other.

    Returns None when the question cannot be answered (a composer error, or a
    recursive alias), so the caller can say what it knows instead of guessing.
    """
    try:
        for document in yaml.compose_all(text):
            stack: list[yaml.Node] = [document] if document is not None else []
            seen: set[int] = set()
            while stack:
                node = stack.pop()
                if id(node) in seen:
                    continue
                seen.add(id(node))
                if isinstance(node, yaml.ScalarNode):
                    if needle in node.value:
                        return True
                elif isinstance(node, yaml.MappingNode):
                    for key_node, value_node in node.value:
                        stack.append(key_node)
                        stack.append(value_node)
                elif isinstance(node, yaml.SequenceNode):
                    stack.extend(node.value)
    except (yaml.YAMLError, RecursionError):
        return None
    return False


# --------------------------------------------------------------------------
# Dispatch.
# --------------------------------------------------------------------------


def scan(relative_path: str, text: str) -> list[SourceLine]:
    """Classify every line of a whole-file SCRIPT in its own language.

    Handles ``.sh`` and ``.ps1``. YAML is not scanned line by line any more -
    :func:`executed_values` parses it instead - so passing a ``.yml`` here
    raises, rather than silently returning an unclassified file.

    Raises :class:`UnknownLanguage` rather than guessing. A silent identity
    fallback would reinstate substring semantics with no visible failure.
    """
    lowered = relative_path.lower()
    executable = list(text)
    code = list(text)
    spans = _line_spans(text)

    if lowered.endswith(".sh"):
        _scan_shell(text, executable, code, 0, len(text))
        context = "shell"
    elif lowered.endswith(".ps1"):
        _scan_powershell(text, executable, code)
        context = "powershell"
    else:
        raise UnknownLanguage(
            f"no scanner for {relative_path!r}. Add one rather than falling back to a "
            "substring test: an unscanned file is a file whose comments are invisible "
            "to this check, which is the exact defect this module replaces."
        )

    executable_joined = "".join(executable)
    code_joined = "".join(code)
    return [
        SourceLine(
            number=index + 1,
            raw=text[start:stop],
            executable=executable_joined[start:stop],
            code=code_joined[start:stop],
            context=context,
            region=0,
        )
        for index, (start, stop) in enumerate(spans)
    ]


def executable_text(lines: list[SourceLine]) -> str:
    return "\n".join(line.executable for line in lines)


def code_text(lines: list[SourceLine]) -> str:
    return "\n".join(line.code for line in lines)


# --------------------------------------------------------------------------
# Reachability, best effort and bounded.
# --------------------------------------------------------------------------

_PARAM_EXPANSION = re.compile(r"\$\{[^}]*\}")

_SH_TERMINATOR = re.compile(r"^(exit|return)\b[ \t]*\$?\w*[ \t]*;?[ \t]*$")
_PS_TERMINATOR = re.compile(r"^(exit|return)\b[ \t]*\$?\w*[ \t]*;?[ \t]*$", re.IGNORECASE)


def _shell_depth_delta(code: str) -> int:
    stripped = _PARAM_EXPANSION.sub(" ", code)
    opens = len(re.findall(r"\bdo\b", stripped))
    opens += len(re.findall(r"\bcase\b", stripped))
    opens += stripped.count("{")
    # `elif ...; then` continues a block rather than opening one.
    if not stripped.lstrip().startswith("elif"):
        opens += len(re.findall(r"\bthen\b", stripped))
    closes = len(re.findall(r"\bfi\b", stripped))
    closes += len(re.findall(r"\bdone\b", stripped))
    closes += len(re.findall(r"\besac\b", stripped))
    closes += stripped.count("}")
    return opens - closes


def _powershell_depth_delta(code: str) -> int:
    stripped = _PARAM_EXPANSION.sub(" ", code)
    return stripped.count("{") - stripped.count("}")


_DEPTH: dict[str, Callable[[str], int]] = {
    "shell": _shell_depth_delta,
    "powershell": _powershell_depth_delta,
}
_TERMINATOR: dict[str, re.Pattern[str]] = {
    "shell": _SH_TERMINATOR,
    "powershell": _PS_TERMINATOR,
}


def _terminator_before(lines: list[SourceLine], target: SourceLine) -> SourceLine | None:
    """An unconditional top-level exit/return that makes ``target`` unreachable.

    Deliberately conservative: only a statement that is the WHOLE line and sits
    at block depth zero counts. `cmd || exit 1` is conditional and is not
    flagged; neither is an exit nested in any block. YAML has no control flow,
    so this never applies there.
    """
    if target.context not in _DEPTH:
        return None
    delta = _DEPTH[target.context]
    terminator = _TERMINATOR[target.context]
    depth = 0
    for line in lines:
        if line.number >= target.number:
            break
        if line.context != target.context or line.region != target.region:
            continue
        if depth == 0 and terminator.match(line.code.strip()):
            return line
        depth = max(depth + delta(line.code), 0)
    return None


# --------------------------------------------------------------------------
# The question callers actually ask.
# --------------------------------------------------------------------------


def invocation_problems(
    relative_path: str,
    text: str,
    needle: str,
    contexts: frozenset[str] | None = None,
    paths: tuple[tuple[tuple[str, ...], bool, bool], ...] | None = None,
) -> list[str]:
    """Every reason ``text`` does not demonstrably EXECUTE ``needle``.

    For a YAML route, ``paths`` names the positions the tool runs and the
    question is answered structurally by a parser. For a whole-file script,
    ``contexts`` names the language and every line is executable code.

    An empty list means at least one occurrence survives comment stripping, sits
    in an executed position, and is not preceded by an unconditional top-level
    exit.
    """
    if relative_path.lower().endswith((".yml", ".yaml")):
        if paths is None:
            raise ValueError(
                f"{relative_path}: no executed-path patterns supplied. Refusing to guess "
                "which positions a tool executes - guessing is what made this check "
                "accept `with: run:`, which never runs."
            )
        return _yaml_problems(relative_path, text, needle, paths)
    return _script_problems(relative_path, text, needle, contexts)


def _yaml_problems(
    relative_path: str,
    text: str,
    needle: str,
    patterns: tuple[tuple[tuple[str, ...], bool], ...],
) -> list[str]:
    """Reject with a reason this function can actually establish.

    An explanation that asserts an unestablished cause is the same defect class
    as a guard claiming coverage it lacks: both teach the reader something
    false, and a rule that looks broken is a rule that gets deleted. So each
    branch below says only what it has shown, and the branches are ordered from
    the most specific establishable cause to the least.
    """
    try:
        values = executed_values(text, patterns)
    except yaml.YAMLError as error:
        # Heuristic, and its worst case is a less specific message rather than a
        # wrong verdict: a multi-document stream is the one parse failure a
        # maintainer is likely to have caused deliberately, so name it.
        multi = "expected a single document" in str(error)
        detail = (
            " The file holds MULTIPLE DOCUMENTS: neither pre-commit nor GitHub Actions "
            "reads past the first, so a value in a later one never runs."
            if multi
            else ""
        )
        return [
            f"{relative_path}: could not be parsed as a single YAML document "
            f"({type(error).__name__}). This check fails CLOSED: a route file the tool "
            f"itself could not read is reported, never certified as correctly wired.{detail} "
            f"{error}"
        ]

    blocked: list[ExecutedValue] = []
    commented_out: list[ExecutedValue] = []
    for value in values:
        if not value.shell:
            if needle in value.value:
                return []
            continue
        # A shell script: a '#' inside it is the shell's comment, which a YAML
        # parser neither can nor should strip.
        lines = scan("body.sh", value.value)
        hits = [line for line in lines if needle in line.executable]
        if hits:
            if any(_terminator_before(lines, line) is None for line in hits):
                return []
            blocked.append(value)
        elif needle in value.value:
            commented_out.append(value)

    if blocked:
        detail = ", ".join(".".join(value.path) for value in blocked)
        return [
            f"{relative_path}: the invocation at {detail} is unreachable after an "
            "unconditional top-level exit earlier in the same script."
        ]

    if commented_out:
        detail = ", ".join(".".join(value.path) for value in commented_out)
        return [
            f"{relative_path}: the invocation is present in the script at {detail} but is "
            "COMMENTED OUT, so that step runs everything except the validator. A '#' "
            "disables this check exactly as deleting the line does."
        ]

    wanted = sorted(".".join(pattern) for pattern, _ in patterns)
    try:
        return _explain(relative_path, text, needle, wanted)
    except Exception as error:  # noqa: BLE001 - deliberate, see below
        # A failure to EXPLAIN must never become a failure to REJECT. The
        # diagnostics walk runs only while building a rejection, so anything
        # that raises in it - a recursive alias today, something else later -
        # would turn a red verdict into a traceback exactly when the guard is
        # doing its job. Catching broadly here is the point: the verdict is
        # already decided above, and only the wording is at risk.
        return [
            f"{relative_path}: {needle!r} is not invoked from any position this route "
            f"executes ({wanted}), and the explanation could not be built "
            f"({type(error).__name__}). Rejecting on what is known."
        ]


def _explain(relative_path: str, text: str, needle: str, wanted: list[str]) -> list[str]:
    """Say why, having established it. May raise; the caller rejects regardless."""
    elsewhere = [path for path, value in all_values(text) if needle in value]
    if elsewhere:
        where = ", ".join(".".join(path) for path in elsewhere)
        return [
            f"{relative_path}: {needle!r} appears at {where}, none of which is a position "
            f"this tool executes. This route runs {wanted}. A value sitting anywhere else "
            "- an action input, an environment variable, a name or a description - is not "
            "a wiring, and neither is a value of the wrong shape, since the tool cannot "
            "load it. Update this rule if the invocation legitimately moved."
        ]

    if needle in text:
        composed = survives_in_the_composed_document(text, needle)
        if composed is True:
            return [
                f"{relative_path}: {needle!r} is written in the document but is not what "
                "runs - the loaded configuration does not contain it, which is what a key "
                "overridden by a later duplicate looks like. The last value of a repeated "
                "key is the one the tool uses."
            ]
        if composed is False:
            return [
                f"{relative_path}: {needle!r} is PRESENT in the file but absent from the "
                "document itself, so it survives only in a YAML comment. Commenting out "
                "the invocation disables the check exactly as deleting it does."
            ]
        return [
            f"{relative_path}: {needle!r} appears in the file but not in the loaded "
            "configuration, so it is not what runs. This check could not determine "
            "whether it is commented out or discarded by the document, because the file "
            "could not be re-read for diagnostics."
        ]

    return [
        f"{relative_path}: {needle!r} is absent entirely; this route no longer runs the "
        "secrets baseline validator."
    ]


def _script_problems(
    relative_path: str, text: str, needle: str, contexts: frozenset[str] | None
) -> list[str]:
    lines = scan(relative_path, text)
    positioned = [
        line
        for line in lines
        if needle in line.executable and (contexts is None or line.context in contexts)
    ]

    if not positioned:
        if needle in text:
            commented = [line.number for line in lines if needle in line.raw]
            return [
                f"{relative_path}: {needle!r} is PRESENT but does not execute - it survives "
                f"only in a comment or in inert data (line(s) {commented}). Commenting out "
                "the invocation disables the check exactly as deleting it does."
            ]
        return [
            f"{relative_path}: {needle!r} is absent entirely; this route no longer runs the "
            "secrets baseline validator."
        ]

    blockers = {line.number: _terminator_before(lines, line) for line in positioned}
    if all(blocker is not None for blocker in blockers.values()):
        detail = ", ".join(
            f"invocation on line {number} is unreachable after the top-level "
            f"{blocker.code.strip()!r} on line {blocker.number}"
            for number, blocker in blockers.items()
            if blocker is not None
        )
        return [f"{relative_path}: {detail}."]

    return []
