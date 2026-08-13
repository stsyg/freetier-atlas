"""Unit tests for the DB-free parts of the scan runner (F005 slice 1).

The full DB-backed runner behaviour (Candidate + official Evidence, per-source
SAVEPOINT isolation, zero offer/offer_version writes) is proven in the
integration suite. Here we cover the pieces that need no database: the
:class:`FetchPolicy` / :class:`FixtureFetcher` construction from a provider
config, the result accounting, the default (offline) fetcher selection, and the
CLI's no-database guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.ingest.fetch import FixtureFetcher, NotFoundError, OfflineFetcher
from app.ingest.runner import (
    RunnerResult,
    SourceScanOutcome,
    _fetcher_for,
    _format_result,
    build_fixture_fetcher,
    fetch_policy_for,
    fixture_mime_for,
    main,
    resolve_fixture_path,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _REPO_ROOT / "config" / "examples" / "providers" / "cloudflare.example.yaml"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "ingest" / "cloudflare" / "html"


def _config() -> ProviderConfig:
    model = load_and_validate(str(_CONFIG))
    assert isinstance(model, ProviderConfig)
    return model


def test_fetch_policy_allowlists_official_domains() -> None:
    policy = fetch_policy_for(_config())
    assert "developers.cloudflare.com" in policy.official_domains


def test_build_fixture_fetcher_registers_nested_source_fixtures() -> None:
    config = _config()
    fetcher = build_fixture_fetcher(config, _FIXTURES)
    assert isinstance(fetcher, FixtureFetcher)

    # The two official HTML limit pages have captured fixtures and resolve
    # offline; sources without a fixture (mcp/rss/pricing) are simply absent.
    workers = next(s for s in config.sources if s.id == "cloudflare-workers-limits")
    result = fetcher.fetch(workers.url)
    assert result.status == 200
    assert b"workers-free-tier" in result.content


def test_build_fixture_fetcher_skips_sources_without_fixture() -> None:
    config = _config()
    fetcher = build_fixture_fetcher(config, _FIXTURES)
    pricing = next(s for s in config.sources if s.id == "cloudflare-pages-pricing")
    # Not registered -> graceful not-found (never a network fetch).
    with pytest.raises(NotFoundError):
        fetcher.fetch(pricing.url)


def test_default_fetcher_is_offline_when_no_fixtures() -> None:
    fetcher = _fetcher_for(_config(), None)
    assert isinstance(fetcher, OfflineFetcher)


def test_runner_result_accounting() -> None:
    result = RunnerResult(
        provider_slug="cloudflare",
        sources=[
            SourceScanOutcome(slug="a", status="scanned", candidates=1),
            SourceScanOutcome(slug="b", status="scanned", candidates=1),
            SourceScanOutcome(slug="c", status="error", error="boom"),
        ],
    )
    assert result.scanned == 2
    assert result.failed == 1
    assert result.total_candidates == 2


def test_zero_source_result_is_an_explicit_success() -> None:
    result = RunnerResult(provider_slug="vercel", configured_sources=0)

    output = _format_result(result)

    assert "zero configured sources; sync and coverage completed" in output
    assert "totals: scanned=0 failed=0 published=0 reviewed=0" in output
    assert "[error]" not in output


def test_main_without_database_url_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code = main([str(_CONFIG)])
    assert code == 2
    assert "no database URL" in capsys.readouterr().err


# --- Multi-format fixture resolution (F008 S3) ------------------------------
#
# Provider adapters are not all HTML: AWS/Azure/Vercel/GitHub sources are JSON
# and RSS. The runner therefore resolves source.html|json|xml (and the flat
# <id>.<ext> layout) and serves each with the MIME its *declared* source type
# implies -- never sniffed, never defaulted. An unresolvable or ambiguous
# fixture is not registered at all, so the fetcher reports not-found rather than
# guessing a content type or reaching the network.


@dataclass(frozen=True)
class _Src:
    """A minimal stand-in for a config source (id / url / type only)."""

    id: str
    url: str | None
    type: str


@dataclass(frozen=True)
class _Provider:
    official_domains: tuple[str, ...] = ("fixtures.example",)


@dataclass(frozen=True)
class _Cfg:
    """A minimal stand-in for a ProviderConfig for fetcher construction."""

    sources: tuple[_Src, ...]
    provider: _Provider = _Provider()


def _write(root: Path, relative: str, body: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


@pytest.mark.parametrize(
    ("source_type", "extension", "expected_mime"),
    [
        ("html", "html", "text/html"),
        ("rss", "xml", "application/rss+xml"),
        ("reference-json", "json", "application/json"),
        ("structured-api", "json", "application/json"),
    ],
)
def test_fixture_mime_is_derived_from_the_declared_source_type(
    source_type: str, extension: str, expected_mime: str
) -> None:
    assert fixture_mime_for(source_type, extension) == expected_mime


def test_xml_is_generic_xml_unless_the_source_declares_rss() -> None:
    assert fixture_mime_for("rss", "xml") == "application/rss+xml"
    assert fixture_mime_for("structured-api", "xml") == "application/xml"
    assert fixture_mime_for(None, "xml") == "application/xml"


def test_an_unknown_extension_yields_no_mime_rather_than_a_default() -> None:
    assert fixture_mime_for("html", "pdf") is None
    assert fixture_mime_for("html", "") is None


@pytest.mark.parametrize(
    ("source_type", "extension", "expected_mime"),
    [
        ("html", "html", "text/html"),
        ("rss", "xml", "application/rss+xml"),
        ("structured-api", "json", "application/json"),
        ("reference-json", "json", "application/json"),
    ],
)
@pytest.mark.parametrize("layout", ["nested", "flat"])
def test_build_fixture_fetcher_serves_every_format_with_the_right_mime(
    tmp_path: Path, source_type: str, extension: str, expected_mime: str, layout: str
) -> None:
    body = b"<fixture-body/>"
    relative = f"a-source/source.{extension}" if layout == "nested" else f"a-source.{extension}"
    _write(tmp_path, relative, body)

    config = _Cfg(sources=(_Src("a-source", "https://fixtures.example/doc", source_type),))
    fetcher = build_fixture_fetcher(config, tmp_path)  # type: ignore[arg-type]

    result = fetcher.fetch("https://fixtures.example/doc")
    assert result.status == 200
    assert result.content == body
    assert result.mime == expected_mime


def test_the_nested_layout_wins_over_the_flat_one(tmp_path: Path) -> None:
    _write(tmp_path, "a-source/source.json", b'{"layout":"nested"}')
    _write(tmp_path, "a-source.json", b'{"layout":"flat"}')

    config = _Cfg(sources=(_Src("a-source", "https://fixtures.example/doc", "structured-api"),))
    fetcher = build_fixture_fetcher(config, tmp_path)  # type: ignore[arg-type]
    assert b"nested" in fetcher.fetch("https://fixtures.example/doc").content


def test_a_source_whose_declared_format_is_absent_is_not_registered(tmp_path: Path) -> None:
    """An RSS source is not served an HTML file that happens to sit there."""

    _write(tmp_path, "a-source/source.html", b"<html/>")
    config = _Cfg(sources=(_Src("a-source", "https://fixtures.example/feed", "rss"),))
    fetcher = build_fixture_fetcher(config, tmp_path)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError):
        fetcher.fetch("https://fixtures.example/feed")


def test_an_mcp_source_has_no_url_document_to_serve(tmp_path: Path) -> None:
    _write(tmp_path, "a-source/source.json", b"{}")
    config = _Cfg(sources=(_Src("a-source", "https://fixtures.example/mcp", "mcp"),))
    fetcher = build_fixture_fetcher(config, tmp_path)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError):
        fetcher.fetch("https://fixtures.example/mcp")


def test_an_undeclared_type_with_one_fixture_resolves(tmp_path: Path) -> None:
    _write(tmp_path, "a-source/source.json", b"{}")
    config = _Cfg(sources=(_Src("a-source", "https://fixtures.example/doc", "something-new"),))
    fetcher = build_fixture_fetcher(config, tmp_path)  # type: ignore[arg-type]
    assert fetcher.fetch("https://fixtures.example/doc").mime == "application/json"


def test_an_undeclared_type_with_ambiguous_fixtures_refuses_to_guess(tmp_path: Path) -> None:
    """Two candidate documents and no declaration: unknown beats guessed."""

    _write(tmp_path, "a-source/source.json", b"{}")
    _write(tmp_path, "a-source/source.html", b"<html/>")
    config = _Cfg(sources=(_Src("a-source", "https://fixtures.example/doc", "something-new"),))
    fetcher = build_fixture_fetcher(config, tmp_path)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError):
        fetcher.fetch("https://fixtures.example/doc")


def test_a_source_without_a_url_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "a-source/source.html", b"<html/>")
    config = _Cfg(sources=(_Src("a-source", None, "html"),))
    assert isinstance(build_fixture_fetcher(config, tmp_path), FixtureFetcher)  # type: ignore[arg-type]


def test_resolve_fixture_path_reports_the_file_and_extension(tmp_path: Path) -> None:
    written = _write(tmp_path, "a-source/source.xml", b"<rss/>")
    assert resolve_fixture_path(tmp_path, "a-source", "rss") == (written, "xml")
    assert resolve_fixture_path(tmp_path, "missing", "rss") is None
    assert resolve_fixture_path(tmp_path, "a-source", "mcp") is None


def test_the_cloudflare_html_path_is_behaviourally_unchanged() -> None:
    """The F005 HTML behaviour must survive the generalisation untouched."""

    config = _config()
    fetcher = build_fixture_fetcher(config, _FIXTURES)
    for source_id in ("cloudflare-workers-limits", "cloudflare-pages-limits"):
        source = next(s for s in config.sources if s.id == source_id)
        result = fetcher.fetch(source.url)
        assert result.mime == "text/html"
        assert result.content == (_FIXTURES / source_id / "source.html").read_bytes()
