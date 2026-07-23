"""Idempotent declarative-config -> database synchronisation (F005 slice 1).

Turns a validated provider configuration (``app.config.models.ProviderConfig``,
loaded from ``config/examples/providers/<provider>.yaml`` via
:func:`app.config.loader.load_and_validate`) into the ORM ``Provider`` and
``Source`` rows the ingestion pipeline scans.

The YAML config and the database use different field names for the same
concepts; this module is the single place that bridges them
(docs/PROVIDER_ADAPTERS.md):

======================  ==============================
YAML (config.models)    database (models.domain.Source)
======================  ==============================
``source.id``           ``slug``   (the idempotent-sync key)
``source.type``         ``adapter_type``
``source.url``          ``endpoint``
``source.extraction_profile``  ``parser_profile``
``source.schedule_ref`` ``schedule``
``source.trust_level``  ``trust_level`` (+ derived ``official`` flag)
======================  ==============================

Idempotency contract: the sync upserts on a *stable key* -- ``Provider.slug``
for the provider and ``Source.slug`` for each source (both UNIQUE). A second run
against a byte-identical config therefore matches the existing rows, changes
nothing, and reports zero created/updated rows. There is **no publication
path**: this module only ever writes ``provider`` and ``source`` rows; it never
touches ``offer`` / ``offer_version`` / ``quota`` and opens no socket.

The caller owns the transaction: :func:`sync_provider` uses ``session.flush()``
(so the new provider id is available for its sources) but never commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.models import ProviderConfig
from app.config.models import Source as SourceConfig
from app.ingest.trust import OFFICIAL_TRUST_LEVEL
from app.models.domain import Provider, Source

#: ``Provider.type`` is required infrastructure metadata with no closed
#: vocabulary and no counterpart in the provider config schema. Until the config
#: gains an explicit provider type this bridge records a neutral default; it is
#: never an offer fact, so "unknown is better than guessed" does not apply.
DEFAULT_PROVIDER_TYPE = "cloud"


@dataclass(frozen=True)
class SourceSyncOutcome:
    """The result of syncing one configured source."""

    slug: str
    action: str  # "created" | "updated" | "unchanged"
    source_id: int | None = None


@dataclass
class SyncResult:
    """A summary of one :func:`sync_provider` run (for idempotency assertions)."""

    provider_slug: str
    provider_id: int | None = None
    provider_action: str = "unchanged"  # "created" | "updated" | "unchanged"
    sources: list[SourceSyncOutcome] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(1 for s in self.sources if s.action == "created")

    @property
    def updated(self) -> int:
        return sum(1 for s in self.sources if s.action == "updated")

    @property
    def unchanged(self) -> int:
        return sum(1 for s in self.sources if s.action == "unchanged")

    @property
    def changed(self) -> bool:
        """True when this run created/updated the provider or any source."""

        return self.provider_action != "unchanged" or self.created > 0 or self.updated > 0


def _desired_source_fields(config: SourceConfig, provider_id: int) -> dict[str, object]:
    """Bridge one YAML source into the ORM column values it maps to."""

    return {
        "provider_id": provider_id,
        "adapter_type": config.type,
        "trust_level": config.trust_level,
        "official": config.trust_level == OFFICIAL_TRUST_LEVEL,
        "endpoint": config.url,
        "schedule": config.schedule_ref,
        "parser_profile": config.extraction_profile,
        "enabled": True,
    }


def _sync_provider_row(session: Session, config: ProviderConfig) -> tuple[Provider, str]:
    section = config.provider
    domains = list(section.official_domains)
    existing = session.execute(
        select(Provider).where(Provider.slug == section.id)
    ).scalar_one_or_none()

    if existing is None:
        provider = Provider(
            slug=section.id,
            name=section.name,
            type=DEFAULT_PROVIDER_TYPE,
            official_domains=domains,
        )
        session.add(provider)
        session.flush()
        return provider, "created"

    changed = False
    if existing.name != section.name:
        existing.name = section.name
        changed = True
    if list(existing.official_domains or []) != domains:
        existing.official_domains = domains
        changed = True
    session.flush()
    return existing, ("updated" if changed else "unchanged")


def _sync_source_row(session: Session, config: SourceConfig, provider_id: int) -> SourceSyncOutcome:
    desired = _desired_source_fields(config, provider_id)
    existing = session.execute(select(Source).where(Source.slug == config.id)).scalar_one_or_none()

    if existing is None:
        source = Source(slug=config.id, **desired)
        session.add(source)
        session.flush()
        return SourceSyncOutcome(slug=config.id, action="created", source_id=source.id)

    changed = False
    for column, value in desired.items():
        if getattr(existing, column) != value:
            setattr(existing, column, value)
            changed = True
    session.flush()
    return SourceSyncOutcome(
        slug=config.id,
        action=("updated" if changed else "unchanged"),
        source_id=existing.id,
    )


def sync_provider(session: Session, config: ProviderConfig) -> SyncResult:
    """Upsert ``config`` into ``provider`` + ``source`` rows; return a summary.

    Idempotent on ``Provider.slug`` and ``Source.slug``: re-running against the
    same config produces zero changes. The caller owns the transaction (this
    flushes but never commits).
    """

    provider, provider_action = _sync_provider_row(session, config)
    result = SyncResult(
        provider_slug=config.provider.id,
        provider_id=provider.id,
        provider_action=provider_action,
    )
    for source_config in config.sources:
        result.sources.append(_sync_source_row(session, source_config, provider.id))
    return result


__all__: Sequence[str] = (
    "DEFAULT_PROVIDER_TYPE",
    "SourceSyncOutcome",
    "SyncResult",
    "sync_provider",
)
