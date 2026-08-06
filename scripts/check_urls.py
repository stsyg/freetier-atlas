#!/usr/bin/env python3
"""Fail when a tracked file contains a URL whose host is not on the allowlist.

Why an allowlist and never a denylist
-------------------------------------
This repository is public. A denylist of forbidden hosts would have to name the
internal hostnames it is meant to exclude, committing them to the public tree and
re-disclosing exactly what the guard exists to remove. An allowlist names only
hosts that are already public, so the guard file itself discloses nothing.

Scope
-----
Every tracked file that is not detected as binary, including lockfiles. Lockfiles
are the highest-value target: a misconfigured package registry writes internal
resolved URLs there, and that has previously required a manual review pass.

Known limits, stated rather than hidden
---------------------------------------
* Only http:// and https:// are inspected. Other schemes carrying a host
  (git://, ssh://, ftp://) are not covered.
* An authority that is not a literal host - a shell/Compose template written in
  place of a hostname - normalises to whatever precedes the first colon and is
  checked like any other host, so it must be allowlisted or removed. An authority
  that does not begin with a valid URL character at all, such as the redaction
  marker ``https://<redacted: ...>``, yields no match, because ``<`` is excluded
  from the authority character class. A placeholder cannot disclose a hostname,
  so this is a deliberate limit.
* Violations are printed with the offending host. In a public repository the
  offending line is already public in the diff by the time this runs, so printing
  it adds no disclosure and makes the failure actionable.

Exit codes: 0 when every URL host is allowlisted, 1 when any is not.
"""

import argparse
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "url-allowlist.txt"

# A bracketed IPv6 literal, or a run of characters that can legally sit in a URL
# authority. Quotes, angle brackets, braces, whitespace and the path separator
# all terminate the authority.
_AUTHORITY = r"(?:\[[0-9A-Fa-f:.]+\]|[^\s/?#\"'`<>()\[\]{}|\\,;]+)"
URL_RE = re.compile(rf"\bhttps?://({_AUTHORITY})", re.IGNORECASE)

BINARY_SNIFF_BYTES = 8192


def load_allowlist(path: pathlib.Path) -> tuple[set[str], list[str]]:
    """Return (exact hosts, suffix domains) parsed from an allowlist file."""
    exact: set[str] = set()
    suffixes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip().lower()
        if not entry:
            continue
        if entry.startswith("."):
            suffixes.append(entry.lstrip("."))
        else:
            exact.add(entry)
    return exact, suffixes


def normalise_host(authority: str) -> str:
    """Reduce a URL authority to a bare lowercase host."""
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    if authority.startswith("[") and "]" in authority:
        host = authority[1 : authority.index("]")]
    else:
        host = authority.split(":", 1)[0]
    return host.rstrip(".").lower()


def is_allowed(host: str, exact: set[str], suffixes: list[str]) -> bool:
    if host in exact:
        return True
    return any(host == s or host.endswith("." + s) for s in suffixes)


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line number, host) for every http(s) URL in ``text``."""
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        for match in URL_RE.finditer(line):
            host = normalise_host(match.group(1))
            if host:
                found.append((lineno, host))
    return found


def tracked_files(root: pathlib.Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\x00") if p]


def scan_repo(root: pathlib.Path) -> list[tuple[str, int, str]]:
    """Return (path, line number, host) for every http(s) URL in tracked text files."""
    hits: list[tuple[str, int, str]] = []
    for rel in tracked_files(root):
        path = root / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\x00" in data[:BINARY_SNIFF_BYTES]:
            continue
        text = data.decode("utf-8", errors="replace")
        hits.extend((rel, lineno, host) for lineno, host in scan_text(text))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every distinct URL host found, with counts, and exit 0",
    )
    args = parser.parse_args()

    exact, suffixes = load_allowlist(ALLOWLIST_PATH)
    hits = scan_repo(REPO_ROOT)

    if args.list:
        counts: dict[str, int] = {}
        for _, _, host in hits:
            counts[host] = counts.get(host, 0) + 1
        for host, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            state = "allowed" if is_allowed(host, exact, suffixes) else "NOT ALLOWED"
            print(f"{count:>6}  {state:<11}  {host}")
        return 0

    violations = [h for h in hits if not is_allowed(h[2], exact, suffixes)]
    if violations:
        print(
            f"URL host check FAILED: {len(violations)} URL(s) use a host that is not on "
            f"the allowlist.",
            file=sys.stderr,
        )
        for rel, lineno, host in violations:
            print(f"  {rel}:{lineno}: {host}", file=sys.stderr)
        print(
            "\nIf the host is genuinely public and intended, add it to "
            f"{ALLOWLIST_PATH.relative_to(REPO_ROOT).as_posix()} in a reviewed commit. "
            "Never add an internal hostname to a public repository.",
            file=sys.stderr,
        )
        return 1

    distinct = len({h[2] for h in hits})
    print(
        f"URL host check passed: {len(hits)} URL(s), {distinct} distinct host(s), all allowlisted."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
