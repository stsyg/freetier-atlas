"""The provider sync is atomic in its own right -- for any failure in its four
writes (F008 savepoint hardening).

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
even if the guarantee broke. Instead each test that asserts on persistence:

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

The one exception, stated rather than glossed over: the rollback-failure test
(``test_a_rollback_that_itself_fails_does_not_displace_the_original``, and its
``add_note``-override variant) asserts on *exception identity*, not on
persistence. It substitutes a fake dead savepoint and rolls the session back
explicitly, so it performs no real ``commit()``; it still uses the fixture's own
engines and its zero-row check still reads the committed state on the separate
connection.

The failure axes are pinned separately: the coverage-floor path, the **source**
write (a non-coverage axis, with a sentinel ``Exception`` subclass), the
**categorisation** write (with a genuine ``BaseException`` that is *not* an
``Exception``, so the breadth of the ``except`` clause is pinned too), a rollback
that itself fails, and an ``add_note`` that fails on top of it. A guard narrowed
to any one of those must go RED.

Two further tests pin the guarantee's **boundary** rather than the guarantee, and
should not be read as evidence of atomicity: a failure raised while the SAVEPOINT
is being *released* lands after ``RELEASE SAVEPOINT`` has already succeeded, so
the writes are the caller's transaction's by then and are committed. That case is
asserted at its documented outcome, and a runtime check asserts that importing
``apps/api/app`` registers no new class-level ``after_transaction_end`` listener
on ``Session`` or a subclass -- which is a tripwire for the realistic regression
rather than proof that the condition holds. See that test's docstring for the
two shapes it deliberately does not catch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
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
from sqlalchemy import create_engine, event, text
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


class _SentinelBaseException(BaseException):
    """A sentinel that is a ``BaseException`` but deliberately **not** an ``Exception``.

    Only a clause with the full ``except BaseException`` breadth catches this, so
    narrowing the guard to ``except Exception`` must make the test using it RED.
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
    ``commit()`` -- against a sync failing inside the four writes, and asserts on
    a separate connection that **nothing** was persisted: not the provider row,
    not its sources, not the coverage rows written before the failure. That
    scoping is load-bearing: a failure raised while the SAVEPOINT is *released*
    is outside this claim, and the boundary test below pins what persists there.

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
        "back and returning would report success for a sync that did not happen"
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
def test_a_base_exception_from_the_categorisation_write_persists_nothing(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two untested things at once: the third write, and ``BaseException`` breadth.

    ``categorise_services()`` is the one write of the four that no other test
    fails, and every sentinel used so far is an ``Exception`` subclass -- so
    narrowing the guard from ``except BaseException`` to ``except Exception``
    left the whole suite green while a caller could commit
    ``{provider: 1, source: 3, coverage: 0}``.

    The sentinel here derives from ``BaseException`` *directly* and is
    deliberately **not** an ``Exception``, so it is caught only by a clause with
    the full breadth. It is raised after the provider row and all three sources
    have already been flushed, which is what makes the partial commit visible if
    the guard misses it. The swallowing caller must therefore catch
    ``BaseException`` too -- a normal ``except Exception`` would not see it.
    """

    config = probe.config()
    assert not issubclass(_SentinelBaseException, Exception), (
        "the sentinel must not be an Exception subclass, or it pins nothing new"
    )

    flushed: dict[str, int] = {}

    def failing_categorise_services(session: Session, cfg: Any) -> Any:
        # Prove the earlier writes really are in the transaction at this point:
        # without the savepoint they are exactly what a swallowing caller commits.
        session.flush()
        flushed["provider"] = session.execute(
            text("SELECT count(*) FROM provider WHERE slug = :slug"),
            {"slug": probe.slug},
        ).scalar_one()
        flushed["source"] = session.execute(
            text(
                "SELECT count(*) FROM source s JOIN provider p ON p.id = s.provider_id "
                "WHERE p.slug = :slug"
            ),
            {"slug": probe.slug},
        ).scalar_one()
        raise _SentinelBaseException("sentinel BaseException from the categorisation write")

    monkeypatch.setattr(config_sync, "categorise_services", failing_categorise_services)

    caught: BaseException | None = None
    returned = None
    with Session(probe.write_engine) as session:
        try:
            returned = sync_provider(session, config)
        except BaseException as exc:  # noqa: BLE001 - the sentinel is not an Exception
            caught = exc
        session.commit()

    assert isinstance(caught, _SentinelBaseException), (
        "a BaseException that is not an Exception must still reach the caller untranslated"
    )
    assert returned is None
    assert flushed == {"provider": 1, "source": len(config.sources)}, (
        "the provider and its sources must already be flushed when the sentinel "
        f"fires, or this test pins nothing; got {flushed}"
    )

    assert probe.committed_counts() == {"provider": 0, "source": 0, "coverage": 0}, (
        "a BaseException from the categorisation write must roll the whole "
        "provider unit back; a guard narrowed to `except Exception` would let "
        "the already-flushed provider and sources commit"
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
def test_a_failing_add_note_on_top_of_a_failing_rollback_still_yields_the_original(
    probe: Probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last link: ``add_note`` is virtually dispatched, so it can raise too.

    The guard's response to a failing rollback is to attach a note to the
    original exception -- but ``add_note`` is an ordinary method that a hostile
    or simply broken exception type can override. With *both* the rollback and
    the note failing, the caller must **still** receive the original exception by
    identity. There is no channel left to report the note failure through, so it
    is discarded deliberately; this test pins that the discarding happens rather
    than the tertiary failure escaping.
    """

    config = probe.config()
    note_failure = RuntimeError("add_note is broken too")

    class _NoteHostileError(RuntimeError):
        def add_note(self, note: str) -> None:
            raise note_failure

    original = _NoteHostileError("the real failure")
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
        with pytest.raises(_NoteHostileError) as excinfo:
            sync_provider(session, config)
        session.rollback()

    assert excinfo.value is original, (
        "neither the rollback failure nor the add_note failure may displace the "
        f"original exception; the caller received {excinfo.value!r}"
    )
    assert excinfo.value is not note_failure
    assert excinfo.value is not rollback_failure

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


@skip_without_db
def test_a_failure_during_savepoint_release_documents_its_boundary(
    probe: Probe,
) -> None:
    """Pins the **boundary** of the atomicity guarantee -- not the guarantee.

    Read this test as documentation of where atomicity stops, never as evidence
    that it holds here. Everything above pins failures raised *by* the four
    writes. This pins the one failure that is outside them: one raised while the
    SAVEPOINT is being **released**.

    SQLAlchemy dispatches ``after_transaction_end`` *after* ``RELEASE SAVEPOINT``
    has already succeeded. By then the four writes belong to the caller's
    enclosing transaction, and ``sync_provider`` -- which does not own that
    transaction -- can no longer revert them, so a swallowing caller's
    ``commit()`` persists the provider even though the sync reported failure.
    That is asserted here as the *documented* outcome rather than an aspirational
    zero, because asserting zero would be asserting a fix that was deliberately
    not built (see the comment at the ``savepoint.commit()`` call for the
    measured options and why each was rejected).

    Two things this still pins, which are the reason it earns its place:

    * the exception handling is unchanged on this path -- the caller receives the
      original exception, by identity;
    * the boundary sits **exactly** at the release. Any failable step a future
      refactor slides in *before* ``savepoint.commit()`` is covered by the tests
      above; if one is ever moved *after* it, the guarantee silently shrinks and
      this test is where that shows up.

    If a future SQLAlchemy changes the dispatch ordering so that the writes no
    longer survive, this test goes RED -- which is the intended signal to revisit
    the documented boundary, not to re-baseline the assertion.
    """

    config = probe.config()
    boom = RuntimeError("after_transaction_end listener failure during release")
    armed = {"on": False}

    caught: BaseException | None = None
    with Session(probe.write_engine) as session:

        @event.listens_for(session, "after_transaction_end")
        def _fail_on_release(_session: Session, transaction: Any) -> None:
            # Only the provider unit's own SAVEPOINT release, not the outer
            # transaction's lifecycle events.
            if armed["on"] and transaction.nested:
                armed["on"] = False
                raise boom

        armed["on"] = True
        try:
            sync_provider(session, config)
        except BaseException as exc:  # noqa: BLE001 - the swallowing caller
            caught = exc
        armed["on"] = False
        session.commit()

    assert caught is boom, (
        "the release-path failure must still reach the caller by identity; the "
        "exception handling is not what this boundary is about"
    )

    assert probe.committed_counts() == {
        "provider": 1,
        "source": len(config.sources),
        "coverage": len(canonical_slugs()),
    }, (
        "DOCUMENTED BOUNDARY, not a guarantee: a failure raised during SAVEPOINT "
        "release lands after RELEASE SAVEPOINT has succeeded, so the writes are "
        "already the caller's transaction's and sync_provider cannot revert them. "
        "If this assertion fails, the boundary described in DATA_MODEL.md and at "
        "the savepoint.commit() call has moved -- re-read that analysis rather "
        "than adjusting these numbers."
    )


def test_no_after_transaction_end_listener_is_registered_in_the_app() -> None:
    """No new class-level ``after_transaction_end`` listener appears on import.

    The release boundary above is only reachable if something registers an
    ``after_transaction_end`` listener; no module under ``apps/`` did so at the
    time of writing, verified by inspection. That is a point-in-time observation
    rather than a standing property -- the two accepted limits below are exactly
    the shapes the repository could not detect it losing. This is
    a **tripwire for the realistic regression** -- someone adding
    ``event.listen(Session, "after_transaction_end", ...)`` to a module -- and is
    deliberately not a proof that the condition holds; see the accepted limits
    below. If it fires, the boundary has stopped being theoretical and a failure
    raised inside that listener can commit a provider whose sync reported
    failure.

    **This asks SQLAlchemy what is registered rather than reading our source.**
    An earlier version of this test scanned for the registration spellings and
    was defeated by ``from sqlalchemy import event as sae`` -- routine Python,
    not an exotic evasion -- and would equally have been defeated by
    ``from sqlalchemy.event import listen``, ``getattr(event, "listen")`` or a
    re-export. No amount of pattern work fixes that, because the question
    "is a listener registered" is not answerable from source text. The runtime
    registry answers it directly, and catches every spelling **at class level and
    import time**, including a dynamically-constructed event name, a registration
    on a ``Session`` subclass or via ``sessionmaker()``, and one made inside a
    function that import happens to call.

    **The tripwire calibrates itself before it is believed.** A tripwire that can
    silently watch nothing enforces nothing: changing the registry read by one
    token, to ``after_transaction_create``, left this test green even with a real
    class-level listener registered. So the probe registers a **sentinel**
    listener of its own, asserts the read observes it, removes it and asserts the
    baseline is restored -- all before the app-import delta is measured. The
    sentinel's event name is written as a **literal**, deliberately not shared
    with the constant in the read: a shared name would drift with it and
    calibrate nothing. The same reasoning covers the other ways this could verify
    nothing -- an empty or truncated sweep (the imported-count assertion), a
    module that failed to import (the failure assertion), and a baseline sampled
    *after* the imports rather than before, which would make the delta empty by
    construction (a canary module, imported as part of the sweep, registers a
    listener that must appear in the delta).

    Three further properties of the mechanism, each deliberate:

    * it runs in a **subprocess with a fresh interpreter**, so the result cannot
      depend on whichever modules pytest happened to import first;
    * it **snapshots the listener set before importing** and asserts that no
      *new* listener appeared, rather than asserting zero. A third-party library
      that legitimately registers one on import is then not a spurious failure --
      the assertion is that *our* code adds none;
    * it imports **every module under** ``apps/api/app``, because a registration
      only exists once the module executes; importing the package root alone
      would see almost nothing.

    ``Session.dispatch.after_transaction_end._clslevel`` is private, and this
    file elsewhere records a refusal to depend on SQLAlchemy internals. The two
    are different in the way that matters: that was a *write* in production code
    faking an internal state transition, where a rename would break the guarantee
    **silently**. This is a *read* in a test, where a rename raises
    ``AttributeError`` and turns this test **loudly** red. It is therefore
    accessed directly and deliberately **without** a ``getattr`` fallback -- a
    fallback would convert that loud failure into a silent one and reintroduce
    exactly the problem this test exists to prevent. SQLAlchemy 2.0 exposes no
    public enumeration API: ``event.contains()`` requires a specific function
    object, so it cannot answer "is *anything* registered".

    **Two registration shapes are known, accepted limits rather than gaps.**
    Both stay GREEN here:

    * a listener attached to an **individual ``Session`` instance**, which lives
      on that instance's dispatch and never reaches the class-level registry;
    * a registration **deferred inside a function that import never executes**,
      which has not happened yet when the snapshot is compared.

    Catching either would require intercepting ``event.listen`` from production
    code -- real complexity in the shipping path to guard a library seam with no
    live trigger -- and that was judged disproportionate. These are documented
    limits, so the claim this test supports is exactly: *importing*
    ``apps/api/app`` registers no new **class-level** ``after_transaction_end``
    listener on ``Session`` or a subclass. It is not a claim that nothing
    anywhere registers one.

    It needs no database, which is why it is not marked ``skip_without_db``.

    If this goes RED, the listener may well be legitimate. The required response
    is to re-examine the documented boundary (and reconsider option (d) at the
    ``savepoint.commit()`` call), not to silence this test.
    """

    app_root = REPO_ROOT / "apps" / "api" / "app"
    assert app_root.is_dir(), f"expected an app package to import: {app_root}"

    modules = sorted(
        ".".join(("app",) + path.relative_to(app_root).with_suffix("").parts)
        .removesuffix(".__init__")
        .replace(".__init__", "")
        for path in app_root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert len(modules) > 20, f"import sweep looks too small to be meaningful: {len(modules)}"

    canary_dir = tempfile.mkdtemp(prefix="f008-listener-probe-")
    canary_name = "_f008_ordering_canary"
    Path(canary_dir, f"{canary_name}.py").write_text(
        "from sqlalchemy import event\n"
        "from sqlalchemy.orm import Session\n"
        "def _f008_ordering_canary_listener(*_args):\n"
        "    return None\n"
        'event.listen(Session, "after_transaction_end", _f008_ordering_canary_listener)\n',
        encoding="utf-8",
    )
    swept = [*modules, canary_name]

    probe_source = textwrap.dedent(
        """
        import importlib
        import json
        import sys

        sys.path.insert(0, sys.argv[1])
        sys.path.insert(0, sys.argv[3])
        from sqlalchemy import event
        from sqlalchemy.orm import Session

        CANARY_MARKER = "_f008_ordering_canary"

        def registered():
            # Direct, unguarded read: see this test's docstring for why a
            # getattr fallback would be actively harmful here.
            clslevel = Session.dispatch.after_transaction_end._clslevel
            return {
                f"{cls.__module__}.{cls.__qualname__}:{fn!r}"
                for cls, fns in clslevel.items()
                for fn in fns
            }

        # CALIBRATION. A registry read that drifts to another event -- one token
        # is enough -- would watch nothing and pass forever. So prove the read
        # observes a registration we make ourselves before trusting it to observe
        # one we did not. The sentinel is registered against the event name as a
        # *literal* here, deliberately not shared with the constant read above:
        # sharing one name would let drift move both together and calibrate
        # nothing.
        def _sentinel(*_args):
            return None

        baseline = registered()
        event.listen(Session, "after_transaction_end", _sentinel)
        calibration_saw_sentinel = bool(registered() - baseline)
        event.remove(Session, "after_transaction_end", _sentinel)
        calibration_clean = registered() == baseline

        # ORDERING POSITIVE CONTROL. ``before`` must be sampled *before* the
        # sweep; taken after it, the delta is empty by construction and this
        # test passes while observing nothing. The canary module is imported as
        # part of the sweep and registers a class-level listener of its own, so
        # a baseline sampled too late loses it from the delta and this fails.
        before = registered()
        failed = {}
        imported = 0
        for name in json.loads(sys.argv[2]):
            try:
                importlib.import_module(name)
            except BaseException as exc:
                failed[name] = f"{type(exc).__name__}: {exc}"
            else:
                imported += 1
        print(
            json.dumps(
                {
                    "new": sorted(
                        entry
                        for entry in registered() - before
                        if CANARY_MARKER not in entry
                    ),
                    "canary_seen": any(
                        CANARY_MARKER in entry for entry in registered() - before
                    ),
                    "failed": failed,
                    "imported": imported,
                    "calibration_saw_sentinel": calibration_saw_sentinel,
                    "calibration_clean": calibration_clean,
                }
            )
        )
        """
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            probe_source,
            str(REPO_ROOT / "apps" / "api"),
            json.dumps(swept),
            canary_dir,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO_ROOT,
    )
    shutil.rmtree(canary_dir, ignore_errors=True)
    assert completed.returncode == 0, (
        "the listener probe subprocess failed; it must run cleanly for its result "
        f"to mean anything.\nstdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])

    # A module that fails to import registers nothing, so an unnoticed import
    # error would make this test vacuously green.
    assert outcome["failed"] == {}, (
        "modules failed to import during the listener sweep, so they were never "
        f"executed and this assertion would be vacuous: {outcome['failed']}"
    )

    # The tripwire must be shown to be watching the right event before its
    # silence is read as evidence. Changing the registry read to another event
    # -- for example after_transaction_create -- otherwise leaves this test
    # green even with a real class-level listener registered.
    assert outcome["calibration_saw_sentinel"], (
        "CALIBRATION FAILED: the registry read did not observe a sentinel "
        "after_transaction_end listener that this probe registered itself, so it "
        "is watching the wrong event (or SQLAlchemy's registry layout changed) "
        "and its silence about the app package means nothing. Fix the read; do "
        "not relax this assertion."
    )
    assert outcome["calibration_clean"], (
        "CALIBRATION FAILED: removing the sentinel did not restore the baseline, "
        "so the delta below is measured against a polluted snapshot."
    )

    # A module that never executed registers nothing, so an empty or truncated
    # sweep would make the delta below vacuous in exactly the same way.
    assert outcome["canary_seen"], (
        "ORDERING CHECK FAILED: the canary module registers a class-level "
        "after_transaction_end listener during the sweep, and it did not appear "
        "in the delta. The baseline snapshot is therefore not being taken before "
        "the imports, which makes the delta empty by construction and the "
        "assertion below vacuous."
    )

    assert outcome["imported"] == len(swept), (
        f"the sweep executed {outcome['imported']} of {len(swept)} modules; a "
        "module that did not run cannot register anything, so the assertion "
        "below would be vacuous"
    )

    assert outcome["new"] == [], (
        "importing the app package registered an after_transaction_end listener: "
        f"{outcome['new']}. The SAVEPOINT-release boundary documented in "
        "DATA_MODEL.md and at the savepoint.commit() call in config_sync.py holds "
        "as a mere library seam only while no such listener exists; with one, a "
        "failure raised during release can commit a provider whose sync reported "
        "failure. Re-examine that boundary before allowing this."
    )
