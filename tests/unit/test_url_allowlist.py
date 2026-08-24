"""Both failure directions of the URL host allowlist guard (``scripts/check_urls.py``).

Why this module exists
----------------------
``scripts/check_urls.py`` is the disclosure guard: it is what keeps an internal
package-feed hostname out of a public repository, and CI runs it on every pull
request. Measured on the base commit, **nothing in the tree tested it** - no test
file imported it, referenced it or invoked it. It was the only build-gating check
this repository owns with zero coverage in *either* direction.

That matters more than a coverage number, because a guard has two ways to fail
and they are not symmetric in cost:

wrongly ACCEPT
    An internal hostname reaches the public tree and the check stays green. The
    damage is disclosure, and it is immediate and irreversible - a public commit
    cannot be un-published.

wrongly REJECT
    The check fires on correct work: a legitimate public host, a redacted
    incident report, a dependency bump that adds a funding URL. The damage lands
    on the INSTRUMENT rather than the artefact, and it lands later - a guard that
    reddens honest pull requests is a guard people learn to route around, and
    then delete. ``scripts/url-allowlist.txt`` says so out loud ("a dependency
    bump can introduce a new host and fail this check. That is intended"), which
    makes this direction a *designed* behaviour and therefore one that has to
    keep working exactly as designed.

The tests below are grouped by direction, and the group headings say which.

The two known limits are pinned rather than repaired
----------------------------------------------------
Two structural blind spots were measured while writing this module. Neither is
repaired here: changing what a security control matches is a behaviour change to
a security control, and it needs an owner decision rather than a test author's.
What this module does instead is convert them from SILENT holes into MONITORED
ones - the limit is asserted to be exactly where the docstring says it is, and
the tree is asserted to contain nothing that exercises it. If either changes, a
human is told.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GUARD_PATH = REPO_ROOT / "scripts" / "check_urls.py"

# scripts/ is not an importable package, so load the guard by path. The module is
# registered in sys.modules BEFORE exec_module: a module that is executed without
# being registered gets a second, distinct copy of itself on any self-import, and
# the failure surfaces far from its cause.
_SPEC = importlib.util.spec_from_file_location("check_urls", GUARD_PATH)
assert _SPEC and _SPEC.loader
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)

#: Suffixes measured across all 639 tracked files on the base commit; the tree is
#: entirely text today. This is deliberately NOT asserted to cover the whole tree:
#: adding a legitimate binary asset (an icon, a screenshot) must not redden the
#: build, which would be precisely the false alarm this module exists to prevent.
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".baseline",
        ".conf",
        ".css",
        ".example",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".mako",
        ".md",
        ".ps1",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)

#: A host used as the "not on the allowlist" case. The final label is not in the
#: IANA root zone, so it can never resolve to anything real, and it is
#: deliberately NOT an RFC 2606 reserved name - every one of those (.test,
#: .example, .invalid, .localhost) is already on the committed allowlist, so
#: using one would make the negative tests pass for the wrong reason.
#:
#: It is only ever interpolated into an f-string, never written as a literal
#: URL: `{` and `}` terminate URL_RE's authority class, so the guard sees no host
#: here and this module's own fixture data cannot trip the check under test.
UNLISTED_HOST = "feed.internal-placeholder.invalid-tld"

#: A JSON-escaped scheme separator, ASSEMBLED FROM PARTS rather than written as a
#: literal. This module asserts that no tracked file contains this byte sequence,
#: and a needle spelled out in full would make that assertion fail on its own
#: source file. That is not a hypothetical: the first two drafts of this module
#: did exactly that, in two different places, and both were caught only by
#: staging the file and re-running - never by re-reading it.
ESCAPED_SEPARATOR = b"\\" + b"/"
ESCAPED_SCHEMES = (
    b"https:" + ESCAPED_SEPARATOR * 2,
    b"http:" + ESCAPED_SEPARATOR * 2,
)


@pytest.fixture(scope="module")
def committed_hits() -> list[tuple[str, int, str]]:
    """Every (path, line, host) the guard finds in the real committed tree."""

    return guard.scan_repo(REPO_ROOT)


@pytest.fixture(scope="module")
def committed_allowlist() -> tuple[set[str], list[str]]:
    return guard.load_allowlist(guard.ALLOWLIST_PATH)


@pytest.fixture(scope="module")
def tracked_tree_survey() -> dict[str, object]:
    """One pass over every tracked file, producing both blind-spot facts.

    Each file's bytes are read ONCE. The two tests below assert different
    properties of that single walk, which is a cost decision and not an evidence
    one: they are separate facts, not two derivations of the same fact.
    """

    escaped: list[str] = []
    nul_text: list[str] = []
    examined = 0
    for rel in guard.tracked_files(REPO_ROOT):
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        # Whole-file byte search, so a URL wrapped across lines cannot hide from it.
        if any(needle in data for needle in ESCAPED_SCHEMES):
            escaped.append(rel)
        if path.suffix.lower() in TEXT_SUFFIXES:
            examined += 1
            if b"\x00" in data[: guard.BINARY_SNIFF_BYTES]:
                nul_text.append(rel)
    return {"escaped": escaped, "nul_text": nul_text, "examined": examined}


def _make_repo(tmp_path: pathlib.Path, files: dict[str, str], allowlist: str) -> pathlib.Path:
    """A throwaway git repository the guard can scan, with its own allowlist."""

    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts" / "url-allowlist.txt").write_text(allowlist, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    # -f so a developer's global excludesFile cannot quietly drop a file and turn
    # a negative test into a vacuous pass.
    subprocess.run(["git", "add", "-A", "-f", "."], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _run(monkeypatch, root: pathlib.Path, argv: tuple[str, ...] = ()) -> int:
    """Point the guard at ``root`` and run its real entry point.

    ``main()`` takes no parameters and parses ``sys.argv`` itself - unlike its
    sibling ``scripts/check_secrets_baseline.py``, whose ``main(argv)`` accepts a
    list. Assuming the two were symmetric is a mistake made and measured while
    writing this file, so the difference is written down here.
    """

    monkeypatch.setattr(guard, "REPO_ROOT", root)
    monkeypatch.setattr(guard, "ALLOWLIST_PATH", root / "scripts" / "url-allowlist.txt")
    monkeypatch.setattr(sys, "argv", ["check_urls.py", *argv])
    return guard.main()


# --------------------------------------------------------------------------
# Direction 1 - wrongly REJECT: the guard must not fire on correct work.
# --------------------------------------------------------------------------


def test_the_committed_tree_passes_the_guard(monkeypatch) -> None:
    """The end-to-end false-alarm guard: honest work must stay green.

    This is the single assertion that catches a change making the check fire on
    a clean tree, which is the failure mode that gets a guard deleted.
    """

    assert _run(monkeypatch, REPO_ROOT) == 0


def test_every_host_in_the_committed_tree_is_allowlisted(
    committed_hits, committed_allowlist
) -> None:
    exact, suffixes = committed_allowlist
    offenders = sorted(
        {host for _, _, host in committed_hits if not guard.is_allowed(host, exact, suffixes)}
    )
    assert not offenders, f"hosts present in the tree but not on the allowlist: {offenders}"


def test_the_committed_scan_is_not_vacuous(committed_hits) -> None:
    """A scan that found nothing would make every assertion above meaningless."""

    hosts = {host for _, _, host in committed_hits}
    files = {path for path, _, _ in committed_hits}
    assert len(committed_hits) >= 500, f"only {len(committed_hits)} URL(s) scanned"
    assert len(hosts) >= 20, f"only {len(hosts)} distinct host(s) found"
    assert any(path.endswith("package-lock.json") for path in files), (
        "no lockfile contributed a URL; the highest-value target is not being read"
    )


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("registry.npmjs.org", "registry.npmjs.org"),
        ("registry.npmjs.org:443", "registry.npmjs.org"),
        ("REGISTRY.NPMJS.ORG", "registry.npmjs.org"),
        ("registry.npmjs.org.", "registry.npmjs.org"),
        ("user:pw@registry.npmjs.org", "registry.npmjs.org"),
        ("[2001:db8::1]:8443", "2001:db8::1"),
    ],
)
def test_an_allowlisted_host_survives_every_normalisation(authority: str, expected: str) -> None:
    """Ports, case, userinfo and a trailing dot must not turn a good host bad."""

    assert guard.normalise_host(authority) == expected


def test_a_suffix_rule_covers_the_domain_and_its_subdomains() -> None:
    exact, suffixes = guard.load_allowlist(guard.ALLOWLIST_PATH)
    for host in ("example.com", "sub.example.com", "a.b.example.com", "anything.test"):
        assert guard.is_allowed(host, exact, suffixes), f"{host} should be allowed"


def test_a_redaction_marker_yields_no_host() -> None:
    """A redacted incident report must stay committable.

    Redacting a hostname is the correct response to having found one. If the
    redaction marker itself tripped the guard, the only way to record the
    incident would be to leave the hostname in - the guard would be arguing for
    the disclosure it exists to prevent.
    """

    assert guard.scan_text("https://<redacted: an internal host>/path") == []


def test_list_mode_reports_without_failing_the_build(tmp_path, monkeypatch, capsys) -> None:
    """``--list`` is a diagnostic. It must never be the thing that reds a build."""

    root = _make_repo(
        tmp_path,
        {"notes.md": f"https://{UNLISTED_HOST}/x\n"},
        "github.com\n",
    )
    assert _run(monkeypatch, root, ("--list",)) == 0
    out = capsys.readouterr().out
    assert UNLISTED_HOST in out and "NOT ALLOWED" in out


def test_allowlist_parsing_ignores_comments_and_blank_lines() -> None:
    exact, suffixes = guard.load_allowlist(guard.ALLOWLIST_PATH)
    assert "#" not in "".join(exact), "a comment leaked into an allowlist entry"
    assert all(entry.strip() == entry and entry for entry in exact)
    assert all(not s.startswith(".") and s for s in suffixes), (
        "suffix entries must be stored without their leading dot"
    )


# --------------------------------------------------------------------------
# Direction 2 - wrongly ACCEPT: the guard must not miss a real disclosure.
# --------------------------------------------------------------------------


def test_an_unlisted_host_fails_the_build(tmp_path, monkeypatch) -> None:
    root = _make_repo(tmp_path, {"notes.md": f"https://{UNLISTED_HOST}/x\n"}, "github.com\n")
    assert _run(monkeypatch, root) == 1


def test_a_lockfile_resolved_url_is_in_scope(tmp_path, monkeypatch) -> None:
    """The stated highest-value target: a misconfigured registry writes here.

    A guard that skipped lockfiles would still pass every prose-shaped test and
    miss the one file that has actually leaked a hostname in practice.
    """

    lockfile = (
        '{\n  "packages": {\n    "node_modules/x": {\n'
        f'      "resolved": "https://{UNLISTED_HOST}/x/-/x-1.0.0.tgz"\n'
        "    }\n  }\n}\n"
    )
    root = _make_repo(tmp_path, {"package-lock.json": lockfile}, "registry.npmjs.org\n")
    assert _run(monkeypatch, root) == 1


def test_a_lookalike_domain_is_not_swallowed_by_a_suffix_rule() -> None:
    """``.example.com`` must match the domain and its subdomains, and nothing else."""

    exact, suffixes = guard.load_allowlist(guard.ALLOWLIST_PATH)
    for host in ("notexample.com", "example.com.attacker.invalid-tld", "fakeexample.com"):
        assert not guard.is_allowed(host, exact, suffixes), f"{host} must not be allowed"


def test_userinfo_cannot_disguise_the_real_host(tmp_path, monkeypatch) -> None:
    """``https://<allowed>@<unlisted>/`` is served by the host AFTER the ``@``."""

    assert guard.normalise_host(f"registry.npmjs.org@{UNLISTED_HOST}") == UNLISTED_HOST
    root = _make_repo(
        tmp_path,
        {"notes.md": f"https://registry.npmjs.org@{UNLISTED_HOST}/x\n"},
        "registry.npmjs.org\n",
    )
    assert _run(monkeypatch, root) == 1


def test_no_allowlist_entry_admits_an_arbitrary_host(committed_allowlist) -> None:
    """A single over-broad entry (a bare public suffix, an empty suffix) would
    silently allow everything while every other test kept passing."""

    exact, suffixes = committed_allowlist
    for host in (UNLISTED_HOST, "internal.corp", "a.b.c.d.invalid-tld", "packages.internal"):
        assert not guard.is_allowed(host, exact, suffixes), f"{host} must not be allowed"


# --------------------------------------------------------------------------
# The two measured blind spots: pinned where they are, and monitored.
# --------------------------------------------------------------------------


def test_a_json_escaped_authority_is_not_matched_and_the_tree_contains_none(
    tracked_tree_survey,
) -> None:
    """LIMIT: a JSON-escaped authority (backslash before each slash) is not matched.

    ``URL_RE`` requires two literal forward slashes, so an authority written with
    escaped separators is invisible to the guard. npm does not emit that form, so
    this is latent rather than live - and the second assertion is what keeps it
    latent. If a file ever commits that spelling, this test says so instead of
    the guard silently reading past it.

    The escaped sample is ASSEMBLED rather than written out, and the docstring
    describes the spelling instead of quoting it. A test that asserts "no tracked
    file contains this byte sequence" must not itself contain that byte sequence,
    or it fails on itself the moment it is tracked - measured, not theorised.
    """

    escaped_sample = (
        '"resolved": "'
        + ESCAPED_SCHEMES[0].decode()
        + "host.invalid"
        + ESCAPED_SEPARATOR.decode()
        + 'x"'
    )
    assert ESCAPED_SCHEMES[0].decode() in escaped_sample, "the sample lost its escaping"
    assert guard.scan_text(escaped_sample) == []

    offenders = tracked_tree_survey["escaped"]
    assert not offenders, (
        f"{offenders} contain a JSON-escaped URL scheme, which scripts/check_urls.py "
        "cannot see. Either rewrite it with literal slashes or widen URL_RE."
    )


def test_the_binary_sniff_skips_whole_files_and_no_tracked_text_file_triggers_it(
    tracked_tree_survey,
) -> None:
    """LIMIT: one NUL byte in the first 8 KiB skips the ENTIRE file, unscanned.

    That is correct for a real binary and wrong for a UTF-16 encoded text file,
    which is an ordinary artefact on Windows - every other byte of ASCII text is
    NUL, so a UTF-16 note containing a hostname is skipped in full.

    The check below is scoped to text suffixes on purpose. Asserting "no tracked
    file is ever skipped" would redden the build the day someone adds a
    screenshot, which is exactly the kind of false alarm this module exists to
    argue against.
    The sample host sits under ``.invalid`` (RFC 2606, and already allowlisted)
    rather than an unlisted namespace: this module's own fixture data is itself
    scanned by the guard once the file is tracked, and an unlisted host here
    would red the very check under test. That is not hypothetical - the first
    draft of this file did exactly that, and ``scripts/check_urls.py`` caught it.
    """

    assert b"\x00" in "https://host.invalid/x".encode("utf-16-le"), (
        "the premise of this test is that UTF-16 text carries NUL bytes"
    )

    examined = tracked_tree_survey["examined"]
    skipped = tracked_tree_survey["nul_text"]
    assert examined >= 100, f"only {examined} text file(s) examined; the control is vacuous"
    assert not skipped, (
        f"{skipped} are text files carrying a NUL byte in their first "
        f"{guard.BINARY_SNIFF_BYTES} bytes, so scripts/check_urls.py skips them entirely "
        "and any URL host inside them is never checked. Re-encode as UTF-8."
    )
