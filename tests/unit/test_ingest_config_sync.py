"""Unit tests for the config->DB field bridging + sync-result accounting (F005).

These exercise the pure, database-free parts of :mod:`app.ingest.config_sync`:
the YAML->ORM field bridge (the exact mapping documented in
docs/PROVIDER_ADAPTERS.md) and the :class:`SyncResult` accounting used by the
idempotency assertions. The full round-trip idempotency proof (no duplicate rows
on re-run) lives in the DB-backed integration suite.
"""

from __future__ import annotations

from app.config.models import Source as SourceConfig
from app.ingest.config_sync import (
    DEFAULT_PROVIDER_TYPE,
    SourceSyncOutcome,
    SyncResult,
    _desired_source_fields,
)


def _source(**overrides: object) -> SourceConfig:
    base: dict[str, object] = {
        "id": "cloudflare-workers-limits",
        "type": "html",
        "trust_level": "official",
        "url": "https://developers.cloudflare.com/workers/platform/limits/",
        "schedule_ref": "official_pages",
        "extraction_profile": "cloudflare_workers_limits",
    }
    base.update(overrides)
    return SourceConfig(**base)


def test_bridge_official_html_source_maps_every_field() -> None:
    fields = _desired_source_fields(_source(), provider_id=7)
    assert fields == {
        "provider_id": 7,
        "adapter_type": "html",  # type -> adapter_type
        "trust_level": "official",
        "official": True,  # derived from trust_level
        "endpoint": "https://developers.cloudflare.com/workers/platform/limits/",  # url -> endpoint
        "schedule": "official_pages",  # schedule_ref -> schedule
        "parser_profile": "cloudflare_workers_limits",  # extraction_profile -> parser_profile
        "enabled": True,
    }


def test_bridge_community_source_is_not_official() -> None:
    fields = _desired_source_fields(_source(trust_level="community"), provider_id=1)
    assert fields["trust_level"] == "community"
    assert fields["official"] is False


def test_bridge_optional_fields_pass_through_as_none() -> None:
    # An MCP source carries no url/profile in the config; the bridge must not
    # invent them -- they map to NULL endpoint/parser_profile.
    fields = _desired_source_fields(
        _source(
            id="cloudflare-docs-mcp",
            type="mcp",
            url=None,
            extraction_profile=None,
            schedule_ref="mcp_documentation",
            capabilities=["documentation_search"],
        ),
        provider_id=1,
    )
    assert fields["adapter_type"] == "mcp"
    assert fields["endpoint"] is None
    assert fields["parser_profile"] is None


def test_default_provider_type_is_neutral_metadata() -> None:
    assert DEFAULT_PROVIDER_TYPE == "cloud"


def test_sync_result_accounting_and_changed_flag() -> None:
    result = SyncResult(
        provider_slug="cloudflare",
        provider_id=1,
        provider_action="unchanged",
        sources=[
            SourceSyncOutcome(slug="a", action="created", source_id=1),
            SourceSyncOutcome(slug="b", action="updated", source_id=2),
            SourceSyncOutcome(slug="c", action="unchanged", source_id=3),
        ],
    )
    assert (result.created, result.updated, result.unchanged) == (1, 1, 1)
    assert result.changed is True


def test_sync_result_unchanged_when_nothing_changed() -> None:
    result = SyncResult(
        provider_slug="cloudflare",
        provider_id=1,
        provider_action="unchanged",
        sources=[
            SourceSyncOutcome(slug="a", action="unchanged", source_id=1),
            SourceSyncOutcome(slug="b", action="unchanged", source_id=2),
        ],
    )
    assert result.changed is False
    assert result.created == 0 and result.updated == 0


def test_sync_result_changed_when_provider_created() -> None:
    result = SyncResult(provider_slug="cloudflare", provider_id=1, provider_action="created")
    assert result.changed is True
