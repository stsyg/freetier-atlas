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
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "SourceLine",
    "UnknownLanguage",
    "code_text",
    "executable_text",
    "invocation_problems",
    "scan",
]


class UnknownLanguage(RuntimeError):
    """No scanner is registered for this file. Raised rather than falling back."""


@dataclass(frozen=True)
class SourceLine:
    """One line of a scanned file.

    ``context`` is the language whose rules produced this line's content, or
    ``None`` for inert data. ``region`` separates independently executed bodies:
    two ``run:`` blocks in a workflow are different regions, so an ``exit`` in
    one says nothing about reachability in the other. ``key`` is the YAML key
    whose VALUE this line contributes to - inline, block scalar or sequence item
    alike - which is the structural fact a route rule needs.
    """

    number: int
    raw: str
    executable: str
    code: str
    context: str | None
    region: int
    key: str | None = None


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
# YAML, and the shell nested inside it.
# --------------------------------------------------------------------------

_YAML_BLOCK = re.compile(
    r"^(?P<indent>[ \t]*)(?:-[ \t]+)?(?P<key>[A-Za-z0-9_.\-]+)[ \t]*:[ \t]*[|>][-+]?\d*[ \t]*$"
)
_YAML_KEY = re.compile(r"^[ \t]*(?:-[ \t]+)?(?P<key>[A-Za-z0-9_.\-]+)[ \t]*:")

#: Block scalars under these keys are SHELL SCRIPTS, so shell comment rules
#: apply to their bodies. Only GitHub Actions' ``run:`` qualifies. pre-commit's
#: ``entry`` is shlex-split and exec'd, NOT shell-interpreted, so a '#' there is
#: a literal argument and stripping it would corrupt the value.
_YAML_SHELL_SCRIPT_KEYS = frozenset({"run"})


def _yaml_comment_start(line: str) -> int | None:
    """Offset of the '#' opening a YAML comment, or None.

    YAML opens a comment only when '#' begins the line or follows whitespace,
    and never inside a quoted scalar.
    """
    single = double = False
    index = 0
    while index < len(line):
        char = line[index]
        if single:
            if char == "'":
                single = False
        elif double:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                double = False
        elif char == "'":
            single = True
        elif char == '"':
            double = True
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return index
        index += 1
    return None


def _blank_yaml_strings(line: str, offset: int, code: list[str], text: str) -> None:
    single = double = False
    start = 0
    for index, char in enumerate(line):
        if single:
            if char == "'":
                _blank(text, offset + start + 1, offset + index, code)
                single = False
        elif double:
            if char == '"':
                _blank(text, offset + start + 1, offset + index, code)
                double = False
        elif char == "'":
            single, start = True, index
        elif char == '"':
            double, start = True, index


def _scan_yaml(
    text: str, executable: list[str], code: list[str], spans: list[tuple[int, int]]
) -> tuple[list[str | None], list[int], list[str | None]]:
    """Classify YAML lines, and record which key OWNS each line's content.

    Ownership is the structural fact the route rules need: a line belongs to the
    value of some key regardless of whether that value is written inline, as a
    block scalar, or as a sequence. Nothing is blanked for being non-script -
    a block scalar under a non-executed key stays visible and is rejected by the
    key rule instead, which is a reason rather than an accident.
    """
    contexts: list[str | None] = ["yaml"] * len(spans)
    regions: list[int] = [0] * len(spans)
    owners: list[str | None] = [None] * len(spans)
    next_region = 1

    index = 0
    while index < len(spans):
        start, stop = spans[index]
        line = text[start:stop]
        match = _YAML_BLOCK.match(line)
        if not match:
            comment = _yaml_comment_start(line)
            if comment is not None:
                _blank(text, start + comment, stop, executable, code)
            _blank_yaml_strings(line, start, code, text)
            index += 1
            continue

        # A block scalar. Its body is every following line that is blank or
        # indented deeper than the key.
        key = match.group("key")
        key_indent = len(match.group("indent"))
        body = index + 1
        while body < len(spans):
            body_start, body_stop = spans[body]
            body_line = text[body_start:body_stop]
            if body_line.strip() and (len(body_line) - len(body_line.lstrip())) <= key_indent:
                break
            body += 1

        if body > index + 1:
            body_start = spans[index + 1][0]
            body_stop = spans[body - 1][1]
            for line_index in range(index + 1, body):
                owners[line_index] = key
            if key in _YAML_SHELL_SCRIPT_KEYS:
                _scan_shell(text, executable, code, body_start, body_stop)
                for line_index in range(index + 1, body):
                    contexts[line_index] = "shell"
                    regions[line_index] = next_region
                next_region += 1
            else:
                # Literal text: no comment syntax applies, so nothing is
                # stripped and nothing is blanked.
                for line_index in range(index + 1, body):
                    contexts[line_index] = "literal"
        index = body

    return contexts, regions, owners


# --------------------------------------------------------------------------
# Dispatch.
# --------------------------------------------------------------------------


def scan(relative_path: str, text: str) -> list[SourceLine]:
    """Classify every line of ``text`` using the language ``relative_path`` implies.

    Raises :class:`UnknownLanguage` rather than guessing. A silent identity
    fallback would reinstate substring semantics with no visible failure.
    """
    lowered = relative_path.lower()
    executable = list(text)
    code = list(text)
    spans = _line_spans(text)

    if lowered.endswith((".yml", ".yaml")):
        contexts, regions, owners = _scan_yaml(text, executable, code, spans)
    elif lowered.endswith(".sh"):
        _scan_shell(text, executable, code, 0, len(text))
        contexts, regions = ["shell"] * len(spans), [0] * len(spans)
        owners = [None] * len(spans)
    elif lowered.endswith(".ps1"):
        _scan_powershell(text, executable, code)
        contexts, regions = ["powershell"] * len(spans), [0] * len(spans)
        owners = [None] * len(spans)
    else:
        raise UnknownLanguage(
            f"no scanner for {relative_path!r}. Add one rather than falling back to a "
            "substring test: an unscanned file is a file whose comments are invisible "
            "to this check, which is the exact defect this module replaces."
        )

    executable_joined = "".join(executable)
    code_joined = "".join(code)

    lines: list[SourceLine] = []
    #: (indent, key) of each mapping key currently open, innermost last. This
    #: computes OWNERSHIP structurally - which key's value a line contributes to
    #: - rather than recognising the spellings that happen to appear in these
    #: four files today. An inline value belongs to the key on its own line; a
    #: block-scalar body belongs to the block's key; a sequence item belongs to
    #: the nearest enclosing key at a shallower indent. All three are the same
    #: question, and fitting only the spelling in front of you is how the 2/18
    #: defect arose.
    open_keys: list[tuple[int, str]] = []
    for index, (start, stop) in enumerate(spans):
        executable_line = executable_joined[start:stop]
        key = owners[index]
        if key is None and contexts[index] == "yaml":
            key_match = _YAML_KEY.match(executable_line)
            indent = len(executable_line) - len(executable_line.lstrip())
            if key_match:
                key = key_match.group("key")
                while open_keys and open_keys[-1][0] >= indent:
                    open_keys.pop()
                open_keys.append((indent, key))
            elif executable_line.strip().startswith("- "):
                for open_indent, open_key in reversed(open_keys):
                    if open_indent < indent:
                        key = open_key
                        break
        lines.append(
            SourceLine(
                number=index + 1,
                raw=text[start:stop],
                executable=executable_line,
                code=code_joined[start:stop],
                context=contexts[index],
                region=regions[index],
                key=key,
            )
        )
    return lines


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
    keys: frozenset[str] | None = None,
) -> list[str]:
    """Every reason ``text`` does not demonstrably EXECUTE ``needle``.

    ``keys`` constrains OWNERSHIP - which key's value the invocation forms - and
    is the structural question a YAML route actually asks. ``contexts``
    constrains the LANGUAGE whose comment rules produced the line, which is a
    lexical fact; it is the right constraint for a whole-file script and the
    WRONG one for YAML, where the same executed position is spelled inline
    (context ``yaml``) or as a block scalar (context ``shell``). Asserting the
    lexical property there rejects 16 of the 18 ways a workflow spells ``run:``.

    An empty list means at least one occurrence survives comment stripping, sits
    in an executed position, and is not preceded by an unconditional top-level
    exit. Any occurrence satisfying all three is enough; the others may be prose.
    """
    lines = scan(relative_path, text)

    positioned = [
        line
        for line in lines
        if needle in line.executable
        and (contexts is None or line.context in contexts)
        and (keys is None or line.key in keys)
    ]

    if not positioned:
        surviving = [line for line in lines if needle in line.executable]
        if surviving:
            where = ", ".join(
                f"line {line.number} (context={line.context}, key={line.key})" for line in surviving
            )
            required = []
            if contexts is not None:
                required.append(f"context in {sorted(contexts)}")
            if keys is not None:
                required.append(f"the value of one of {sorted(keys)}")
            return [
                f"{relative_path}: {needle!r} survives comment stripping but not in an "
                f"executed position. Found at {where}; this route requires "
                + " and ".join(required or ["an executed position"])
                + ". Update this rule if the invocation legitimately moved."
            ]
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
