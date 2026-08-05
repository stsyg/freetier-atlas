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
  fails**, deleting strictly by captured ownership ids so it can never remove a
  row it did not create, and asserting the result against those ids rather than
  against the predicate it deleted by.

The failure axes are pinned separately: the coverage-floor path, a **non-coverage**
write (the source write, with a sentinel exception type), and a rollback that
itself fails. A guard narrowed to any one of those must go RED.
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
from app.ingest import config_sync
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


class _SentinelSourceWriteError(RuntimeError):
    """A failure type the savepoint guard has no special knowledge of.

    Deliberately *not* :class:`CoverageFloorError`: a guard narrowed to the
    coverage axis must not be able to catch it.
    """


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
        """Read this provider's persisted rows on a wholly separate connection.

        Every count is **ownership-scoped**: sources are counted through their
        ``provider_id`` rather than by a slug prefix, so a foreign row that
        merely happens to share the prefix cannot satisfy -- or disturb -- these
        assertions.
        """

        with self.read_engine.connect() as conn:
            providers = conn.execute(
                text("SELECT count(*) FROM provider WHERE slug = :slug"),
                {"slug": self.slug},
            ).scalar_one()
            sources = conn.execute(
                text(
                    "SELECT count(*) FROM source s "
                    "JOIN provider p ON p.id = s.provider_id WHERE p.slug = :slug"
                ),
                {"slug": self.slug},
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

    Two distinct properties, which are worth not conflating:

    * the slug is unique per run, so no other test or fixture can have attached
      a row to this provider -- that prevents *collision*;
    * the teardown deletes strictly by **ownership** (``provider_id``, resolved
      from this provider's row) and never by slug prefix -- that prevents
      deleting a row this fixture did not create, which slug uniqueness alone
      would *not* prevent.

    ``source.provider_id`` is ``ON DELETE SET NULL``, so deleting the provider
    does not remove its sources; they are deleted first, while the ownership
    link still exists. The leftover assertion afterwards is deliberately made
    against the **captured row ids**, not against the predicate the delete used,
    so it cannot be self-satisfying.
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
            with read_engine.begin() as conn:
                provider_ids = list(
                    conn.execute(
                        text("SELECT id FROM provider WHERE slug = :slug"),
                        {"slug": slug},
                    ).scalars()
                )
                source_ids = (
                    list(
                        conn.execute(
                            text("SELECT id FROM source WHERE provider_id = ANY(:ids)"),
                            {"ids": provider_ids},
                        ).scalars()
                    )
                    if provider_ids
                    else []
                )
                conn.execute(
                    text("DELETE FROM provider_category_coverage WHERE provider_id = ANY(:ids)"),
                    {"ids": provider_ids},
                )
                conn.execute(
                    text("DELETE FROM source WHERE provider_id = ANY(:ids)"),
                    {"ids": provider_ids},
                )
                conn.execute(text("DELETE FROM provider WHERE slug = :slug"), {"slug": slug})
            with read_engine.connect() as conn:
                leftovers = {
                    "provider": conn.execute(
                        text("SELECT count(*) FROM provider WHERE id = ANY(:ids)"),
                        {"ids": provider_ids},
                    ).scalar_one(),
                    "source": conn.execute(
                        text("SELECT count(*) FROM source WHERE id = ANY(:ids)"),
                        {"ids": source_ids},
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
def test_a_failure_outside_the_coverage_block_also_persists_nothing(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomicity is pinned on a **non-coverage** axis too, not only the floor.

    The coverage-floor test above only exercises the last of the four writes, so
    a savepoint narrowed to ``except CoverageFloorError`` would still pass it
    while committing a partial provider on every other path. Here the failure is
    raised from the **source** write -- after the provider row and the first
    source have already been flushed -- with an exception type the guard has no
    special knowledge of. The whole unit must still vanish.
    """

    config = probe.config()
    assert len(config.sources) > 1, "the sentinel must fire after at least one source is written"

    real_sync_source_row = config_sync._sync_source_row
    calls = {"n": 0}

    def failing_sync_source_row(session: Session, source_config: Any, provider_id: int) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise _SentinelSourceWriteError("sentinel failure on the source write")
        return real_sync_source_row(session, source_config, provider_id)

    monkeypatch.setattr(config_sync, "_sync_source_row", failing_sync_source_row)

    caught: BaseException | None = None
    returned = None
    with Session(probe.write_engine) as session:
        try:
            returned = sync_provider(session, config)
        except Exception as exc:  # noqa: BLE001 - deliberately the swallowing caller
            caught = exc
        session.commit()

    assert isinstance(caught, _SentinelSourceWriteError), (
        "the original exception must reach the caller untranslated, whatever its type"
    )
    assert returned is None
    assert calls["n"] == 2, "the sentinel must have fired after a successful first source write"

    assert probe.committed_counts() == {"provider": 0, "source": 0, "coverage": 0}, (
        "a failure on the source write must roll the whole provider unit back, not "
        "only failures that come from the coverage block"
    )


@skip_without_db
def test_a_rollback_that_itself_fails_does_not_displace_the_original(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secondary rollback failure must not become what the caller sees.

    If the SAVEPOINT is already gone by the time the guard runs, ``rollback()``
    raises in its own right (SQLAlchemy reports ``ResourceClosedError`` for a
    closed nested transaction). The caller must still receive the **original**
    exception, unchanged in type *and* identity; the rollback failure is
    attached to it as a note rather than discarded.
    """

    config = probe.config()
    original = _SentinelSourceWriteError("the real failure")
    rollback_failure = RuntimeError("this transaction is closed")

    class _DeadSavepoint:
        is_active = False

        def commit(self) -> None:  # pragma: no cover - this test always fails first
            raise AssertionError("unreachable: the unit fails before it commits")

        def rollback(self) -> None:
            raise rollback_failure

    def failing_sync_provider_row(session: Session, cfg: Any) -> Any:
        raise original

    monkeypatch.setattr(config_sync, "_sync_provider_row", failing_sync_provider_row)

    with Session(probe.write_engine) as session:
        monkeypatch.setattr(session, "begin_nested", lambda: _DeadSavepoint())
        with pytest.raises(_SentinelSourceWriteError) as excinfo:
            sync_provider(session, config)
        session.rollback()

    assert excinfo.value is original, "the caller must receive the original exception object itself"
    notes = getattr(excinfo.value, "__notes__", [])
    assert any("SAVEPOINT" in note and repr(rollback_failure) in note for note in notes), (
        f"the suppressed rollback failure must be attached, not discarded; notes={notes}"
    )

    assert probe.committed_counts() == {"provider": 0, "source": 0, "coverage": 0}


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
