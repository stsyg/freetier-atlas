"""Unit tests for the DB-free parts of the scan runner (F005 slice 1).

The full DB-backed runner behaviour (Candidate + official Evidence, per-source
SAVEPOINT isolation, zero offer/offer_version writes) is proven in the
integration suite. Here we cover the pieces that need no database: the
:class:`FetchPolicy` / :class:`FixtureFetcher` construction from a provider
config, the result accounting, the default (offline) fetcher selection, and the
CLI's no-database guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config.loader import load_and_validate
from app.config.models import ProviderConfig
from app.ingest.fetch import FixtureFetcher, NotFoundError, OfflineFetcher
from app.ingest.runner import (
    RunnerResult,
    SourceScanOutcome,
    _fetcher_for,
    build_fixture_fetcher,
    fetch_policy_for,
    main,
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


def test_main_without_database_url_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code = main([str(_CONFIG)])
    assert code == 2
    assert "no database URL" in capsys.readouterr().err
