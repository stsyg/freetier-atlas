"""Pre-fix inventory of the App Service "one unknown / card gate alone" claim.

Built BEFORE the correction lands, because the inventory is unobtainable
afterwards: once the text is corrected the occurrences are gone, and verifying
"every occurrence was fixed" would mean trusting someone's list rather than
re-deriving it. Two counts are already in circulation for this - 6 and 9 - and
both are claims until measured.

"Occurrence" is ambiguous, so this reports THREE explicit granularities rather
than a single number that would have to be argued about:

  FILES     - distinct files carrying at least one claim
  LINES     - distinct line numbers matching at least one pattern
  MATCHES   - individual pattern hits (a line asserting two things counts twice)

Patterns are split into FALSE claims (must all disappear or be reworded) and
TRUE-but-adjacent claims (correct as written; must NOT be "corrected" away).

Usage:  python inventory_one_unknown_claim.py <repo_root> [git_sha]
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Claims that are FALSE against the measured classifier output.
FALSE_CLAIMS = {
    "one_unknown": re.compile(r"\bone unknown separates it from z0", re.I),
    "card_gate_alone": re.compile(r"card gate\b[^.\n]{0,20}\balone", re.I),
    "fails_only_card": re.compile(
        r"fails\s+\*{0,2}only\*{0,2}[^.\n]{0,160}?(payment card|card is required)", re.I
    ),
    "test_name_only_card": re.compile(r"fails_only_on_the_card_gate", re.I),
}

# Statements that are TRUE as written. Recorded so a reviewer can confirm the
# correction did not over-reach and delete accurate text along with the wrong.
TRUE_ADJACENT = {
    "clears_billing_gate": re.compile(r"clears the billing gate entirely", re.I),
    "fails_only_at_unknown_gate": re.compile(
        r"fails\s+\*{0,2}only\*{0,2}\s+at the unknown-conditions gate", re.I
    ),
}

SEARCH_ROOTS = ("apps", "config", "docs", "tests", "agent-state")
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".ico", ".woff", ".woff2"}


def tracked_files(repo: Path, sha: str | None) -> list[Path]:
    if sha:
        out = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", sha],
            capture_output=True,
            text=True,
            cwd=repo,
        )
        if out.returncode != 0:
            raise SystemExit(f"git ls-tree failed: {out.stderr.strip()}")
        names = out.stdout.splitlines()
    else:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=repo)
        names = out.stdout.splitlines()

    files = []
    for name in names:
        if not name or Path(name).suffix in SKIP_SUFFIXES:
            continue
        if not name.startswith(SEARCH_ROOTS):
            continue
        if any(part in SKIP_DIRS for part in Path(name).parts):
            continue
        files.append(Path(name))
    return files


def read(repo: Path, path: Path, sha: str | None) -> str | None:
    if sha:
        out = subprocess.run(
            ["git", "show", f"{sha}:{path.as_posix()}"],
            capture_output=True,
            cwd=repo,
        )
        if out.returncode != 0:
            return None
        raw = out.stdout
    else:
        try:
            raw = (repo / path).read_bytes()
        except OSError:
            return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def normalise(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace (and comment/continuation markers) to single spaces.

    Returns the normalised text plus a map from each normalised character index
    back to the ORIGINAL character offset, so a match can be reported at the line
    where it starts. This is what makes the scan wrap-aware: these claims are
    prose wrapped across lines in YAML comments, Python docstrings and Markdown,
    and a line-based regex silently misses every wrapped one -- an instrument that
    has lost part of its subject looks exactly like one whose subject is clean.
    """

    out: list[str] = []
    index: list[int] = []
    pending_space = False
    for offset, char in enumerate(text):
        if char.isspace():
            pending_space = bool(out)
            continue
        if pending_space:
            out.append(" ")
            index.append(offset)
            pending_space = False
        out.append(char)
        index.append(offset)
    return "".join(out), index


def line_of(line_starts: list[int], offset: int) -> int:
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def scan(repo: Path, sha: str | None, patterns: dict[str, re.Pattern[str]]):
    """Scan wrap-aware, flagging hits a line-based regex could never have seen."""

    hits = []
    for path in tracked_files(repo, sha):
        text = read(repo, path, sha)
        if text is None:
            continue

        line_starts = [0]
        for offset, char in enumerate(text):
            if char == "\n":
                line_starts.append(offset + 1)

        flat, index = normalise(text)
        for label, pattern in patterns.items():
            for match in pattern.finditer(flat):
                start = index[match.start()] if match.start() < len(index) else 0
                end = index[min(match.end(), len(index) - 1)]
                lineno = line_of(line_starts, start)
                wrapped = line_of(line_starts, end) != lineno
                excerpt = " ".join(match.group(0).split())
                hits.append((path.as_posix(), lineno, label, excerpt, wrapped))
    return hits


def report(title: str, hits) -> None:
    print("=" * 78)
    print(title)
    print("=" * 78)
    files = sorted({h[0] for h in hits})
    lines = {(h[0], h[1]) for h in hits}
    for path in files:
        print(f"\n  {path}")
        for _, lineno, label, text, wrapped in sorted(h for h in hits if h[0] == path):
            excerpt = text if len(text) <= 110 else text[:107] + "..."
            flag = " WRAPPED (invisible to a line-based scan)" if wrapped else ""
            print(f"    L{lineno:<6} [{label}]{flag}")
            print(f"      {excerpt}")
    print()
    print(f"  FILES   : {len(files)}")
    print(f"  LINES   : {len(lines)}")
    print(f"  MATCHES : {len(hits)}")
    print(f"  of which WRAPPED across lines: {sum(1 for h in hits if h[4])}")
    print()


def main() -> None:
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    sha = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"repo={repo}  sha={sha or '<working tree>'}\n")

    false_hits = scan(repo, sha, FALSE_CLAIMS)
    report("FALSE CLAIMS - every one must be corrected", false_hits)

    true_hits = scan(repo, sha, TRUE_ADJACENT)
    report("TRUE ADJACENT STATEMENTS - must survive the correction", true_hits)

    print("=" * 78)
    print("SUMMARY (compare against any circulated count)")
    print("=" * 78)
    print(f"  false-claim FILES   : {len({h[0] for h in false_hits})}")
    print(f"  false-claim LINES   : {len({(h[0], h[1]) for h in false_hits})}")
    print(f"  false-claim MATCHES : {len(false_hits)}")
    print(f"  true-adjacent LINES : {len({(h[0], h[1]) for h in true_hits})}")


if __name__ == "__main__":
    main()
