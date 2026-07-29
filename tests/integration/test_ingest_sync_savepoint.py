"""The provider sync is atomic in its own right (F008 savepoint hardening).

``sync_coverage()`` protects the Q9-A evidence floor by *raising*
(:class:`~app.ingest.config_sync.CoverageFloorError`). That only prevents the
erosion because the already-flushed writes die with the transaction -- which,
before this slice, was a property of the **callers** rather than of the code.
``sync_provider`` never commits, its only non-test caller sits outside any
``try``/``except``, and the CLI commits once after the loop. A future caller
shaped ``try: sync_provider(...) except Exception: continue`` followed by a
``commit()`` -- the shape ``app.ingest.runner`` already uses per source -- would
have committed exactly the half-synced provider the raise was meant to prevent.

These tests pin the property so such a caller goes RED instead of degrading
silently. **They deliberately do not use the rolled-back ``session`` fixture from
``test_ingest_config_sync``**: that fixture binds to a connection inside an outer
transaction which teardown rolls back, so a test written against it would pass
even if the guarantee broke. Instead each test:

* takes its **own engine** for the write side and a **second, independent
  engine** for the read-back, so the assertions cannot be satisfied by a shared
  connection's uncommitted state or by a session's identity map;
* performs a **real ``commit()``** in the caller's position, exactly as the CLI
  does;
* operates on a **unique per-run provider slug** it creates and destroys itself,
  never ``cloudflare`` -- the hazard is slug collision, and a shared slug would
  make the teardown cascade depend on what other tests happened to run first;
* tears down explicitly, on a connection of its own, **even when the test body
  fails**, and asserts empirically that nothing of its own is left behind.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic import command
from alembic.config import Config
from app.config.loader import load_and_validate
from app.config.models import MIN_EVIDENCE_BACKED_COVERAGE, ProviderConfig
from app.ingest.config_sync import CoverageFloorError, sync_provider
from app.read_api.taxonomy import canonical_slugs
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


def _synthetic_provider_document(slug: str) -> dict[str, Any]:
    """A complete, *valid* provider config for a throwaway provider slug.

    Deliberately built as a real document and loaded through
    :func:`app.config.loader.load_and_validate`, so the fourteen-canonical-slug
    rule and the Q9-A floor validator are genuinely exercised rather than
    bypassed by hand-constructing the model.

    Exactly ``MIN_EVIDENCE_BACKED_COVERAGE`` categories are evidence-backed, and
    every one of them cites a ``source`` rather than an ``evidence_url``. That is
    what lets a test drop the sources and land the provider below the persisted
    floor with no other change.
    """

    slugs = sorted(canonical_slugs())
    backed = slugs[:MIN_EVIDENCE_BACKED_COVERAGE]
    sources = [
        {
            "id": f"{slug}-source-{index}",
            "type": "html",
            "trust_level": "official",
            # Never fetched: this suite syncs config into the database and opens
            # no socket. `.invalid` is reserved by RFC 2606 and cannot resolve.
            "url": f"https://{slug}.invalid/limits/{index}",
            "schedule_ref": "official_pages",
            "extraction_profile": "pricing_document",
        }
        for index, _ in enumerate(backed, start=1)
    ]
    coverage: dict[str, dict[str, str]] = {name: {"state": "unknown"} for name in slugs}
    for index, name in enumerate(backed, start=1):
        coverage[name] = {"state": "verified_free", "source": f"{slug}-source-{index}"}

    return {
        "provider": {
            "id": slug,
            "name": f"Savepoint Probe {slug}",
            "official_domains": [f"{slug}.invalid"],
        },
        "sources": sources,
        "publishing": {
            "automatic_threshold": 0.90,
            "uncertain_threshold": 0.70,
            "require_official_source": True,
            "require_deterministic_numeric_validation": True,
        },
        # A declared mapping for a service that does not exist: a recorded no-op
        # that still exercises the categorise_services() write inside the unit.
        "service_categories": {f"Probe Service {slug}": backed[0]},
        "coverage": coverage,
    }


@dataclass(frozen=True)
class Probe:
    """One throwaway provider: its slug, a write engine and a read-back engine."""

    slug: str
    write_engine: Engine
    read_engine: Engine
    config_path: Path

    def config(self) -> ProviderConfig:
        model = load_and_validate(str(self.config_path))
        assert isinstance(model, ProviderConfig)
        return model

    def committed_counts(self) -> dict[str, int]:
        """Read this provider's persisted rows on a wholly separate connection."""

        like = f"{self.slug}%"
        with self.read_engine.connect() as conn:
            providers = conn.execute(
                text("SELECT count(*) FROM provider WHERE slug = :slug"),
                {"slug": self.slug},
            ).scalar_one()
            sources = conn.execute(
                text("SELECT count(*) FROM source WHERE slug LIKE :like"),
                {"like": like},
            ).scalar_one()
            coverage = conn.execute(
                text(
                    "SELECT count(*) FROM provider_category_coverage c "
                    "JOIN provider p ON p.id = c.provider_id WHERE p.slug = :slug"
                ),
                {"slug": self.slug},
            ).scalar_one()
        return {"provider": providers, "source": sources, "coverage": coverage}


@pytest.fixture
def probe(tmp_path: Path) -> Iterator[Probe]:
    """A unique provider slug with its own engines and an explicit teardown.

    The slug is unique per run, so the teardown below is safe **by construction**
    rather than by assumption about what else ran: no other test or fixture can
    have attached a row to it. ``source.provider_id`` is ``ON DELETE SET NULL``,
    so deleting the provider does *not* remove its sources -- they are deleted
    explicitly, before the provider, and the result is verified rather than
    assumed. The slug contains no ``%`` or ``_``, so the ``LIKE`` patterns above
    carry no wildcard.
    """

    command.upgrade(_alembic_config(), "head")
    slug = f"f008-probe-{uuid.uuid4().hex[:12]}"
    document = _synthetic_provider_document(slug)
    config_path = tmp_path / f"{slug}.yaml"
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    write_engine = create_engine(DATABASE_URL)
    read_engine = create_engine(DATABASE_URL)
    try:
        yield Probe(
            slug=slug,
            write_engine=write_engine,
            read_engine=read_engine,
            config_path=config_path,
        )
    finally:
        try:
            like = f"{slug}%"
            with read_engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM provider_category_coverage WHERE provider_id IN "
                        "(SELECT id FROM provider WHERE slug = :slug)"
                    ),
                    {"slug": slug},
                )
                conn.execute(text("DELETE FROM source WHERE slug LIKE :like"), {"like": like})
                conn.execute(text("DELETE FROM provider WHERE slug = :slug"), {"slug": slug})
            with read_engine.connect() as conn:
                leftovers = {
                    "provider": conn.execute(
                        text("SELECT count(*) FROM provider WHERE slug = :slug"),
                        {"slug": slug},
                    ).scalar_one(),
                    "source": conn.execute(
                        text("SELECT count(*) FROM source WHERE slug LIKE :like"),
                        {"like": like},
                    ).scalar_one(),
                }
            assert leftovers == {"provider": 0, "source": 0}, (
                f"teardown left rows behind for {slug}: {leftovers}"
            )
        finally:
            write_engine.dispose()
            read_engine.dispose()


@skip_without_db
def test_a_caller_that_swallows_the_failure_and_commits_persists_nothing(
    probe: Probe,
) -> None:
    """The CLI shape must not be able to commit a half-synced provider.

    Reproduces the batch-runner shape Wave 3 makes likely -- ``try:
    sync_provider(...) except Exception: continue`` followed by a real
    ``commit()`` -- against a genuinely failing sync, and asserts on a separate
    connection that **nothing** was persisted: not the provider row, not its
    sources, not the coverage rows written before the failure.

    The failure is the documented partially-synced-database case: the config's
    only evidence-backed declarations cite sources, so removing the sources makes
    every one of them unresolvable and the persisted rows fall below the Q9-A
    floor. Before the savepoint the provider row and the eleven resolvable
    coverage rows were already flushed by then, so this commit persisted them.
    """

    config = probe.config()
    # A partially synced database: the provider is being written, its sources are
    # not there yet. Same idiom the F008 obs-A floor tests use.
    config.sources = []

    caught: Exception | None = None
    returned = None
    with Session(probe.write_engine) as session:
        try:
            returned = sync_provider(session, config)
        except Exception as exc:  # noqa: BLE001 - deliberately the swallowing caller
            caught = exc
        # The caller learns nothing from the failure and commits anyway.
        session.commit()

    assert isinstance(caught, CoverageFloorError), (
        "the guard must still reach the caller as CoverageFloorError, untranslated"
    )
    assert returned is None, (
        "a rolled-back sync must never return a success-shaped SyncResult; rolling "
        "back and returning would report success over expired ORM objects"
    )

    assert probe.committed_counts() == {"provider": 0, "source": 0, "coverage": 0}, (
        "a failed sync_provider must leave the database untouched even when the "
        "caller swallows the exception and commits"
    )


@skip_without_db
def test_a_successful_sync_still_commits_every_write(probe: Probe) -> None:
    """The savepoint must be invisible when nothing fails.

    The mirror of the test above: without it, 'nothing was persisted' would also
    be satisfied by a savepoint that silently discarded good writes.
    """

    config = probe.config()

    with Session(probe.write_engine) as session:
        result = sync_provider(session, config)
        assert result.provider_action == "created"
        assert result.created == len(config.sources)
        assert result.coverage is not None
        assert result.coverage.created == len(canonical_slugs())
        session.commit()

    assert probe.committed_counts() == {
        "provider": 1,
        "source": len(config.sources),
        "coverage": len(canonical_slugs()),
    }

    # And the unit is still idempotent through a second committed run.
    with Session(probe.write_engine) as session:
        second = sync_provider(session, config)
        assert second.changed is False
        session.commit()

    assert probe.committed_counts() == {
        "provider": 1,
        "source": len(config.sources),
        "coverage": len(canonical_slugs()),
    }
