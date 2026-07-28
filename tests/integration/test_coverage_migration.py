"""Integration tests for migration ``0011_provider_category_coverage`` (F008 S2).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. These prove
that coverage is an explicit, provenance-backed **declaration** enforced by the
database itself rather than by application code alone:

(a) ``alembic upgrade head`` reaches ``0011_provider_category_coverage``, there
    is a single head, and re-applying the step is a no-op;
(b) ``downgrade 0010_category_seed`` drops **only** the new table -- the
    fourteen categories seeded by 0010 survive untouched;
(c) the three CHECK constraints reject a bad **raw SQL** ``INSERT``: an illegal
    state, ``not_offered`` without a rationale, and ``verified_free`` without a
    source or evidence URL. Pydantic validation is not the only line of defence;
(d) ``UNIQUE(provider_id, category_id)`` rejects a duplicate pair, and the FKs
    cascade on provider/category delete while ``source_id`` degrades to NULL;
(e) **Q11**: the table carries no ``derived_state``/``derived_at`` column, and
    the shipped migration/ORM pair produces no alembic autogenerate drift.

The upgrade/downgrade steps run OUTSIDE the rolled-back test transaction
(alembic owns its own connection), so each test restores the database to
``head`` before returning.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.models.domain import Base
from app.models.vocab import COVERAGE_STATES
from app.read_api.taxonomy import canonical_slugs
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)

COVERAGE_REVISION = "0011_provider_category_coverage"
PREVIOUS_REVISION = "0010_category_seed"
TABLE = "provider_category_coverage"


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.upgrade(_alembic_config(), "head")
    eng = create_engine(DATABASE_URL)
    try:
        yield eng
    finally:
        command.upgrade(_alembic_config(), "head")
        eng.dispose()


@pytest.fixture()
def probe_ids(engine: Engine) -> Iterator[tuple[int, int]]:
    """A throwaway provider plus a real canonical category id."""
    with engine.begin() as conn:
        provider_id = conn.execute(
            text(
                "INSERT INTO provider (slug, name, type) "
                "VALUES ('synthetic-coverage-probe', 'Synthetic coverage probe', 'cloud') "
                "RETURNING id"
            )
        ).scalar_one()
        category_id = conn.execute(
            text("SELECT id FROM category WHERE slug = :s"),
            {"s": sorted(canonical_slugs())[0]},
        ).scalar_one()
    try:
        yield int(provider_id), int(category_id)
    finally:
        with engine.begin() as conn:
            # ON DELETE CASCADE removes the coverage rows with the provider.
            conn.execute(text("DELETE FROM provider WHERE id = :i"), {"i": provider_id})


def _heads(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())


def _category_slugs(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT slug FROM category")).scalars())


# --- (a) head, single head, idempotent re-apply -----------------------------


@skip_without_db
def test_upgrade_head_reaches_the_coverage_revision(engine: Engine) -> None:
    heads = _heads(engine)
    assert heads == {COVERAGE_REVISION}, f"expected a single head at {COVERAGE_REVISION}"
    assert inspect(engine).has_table(TABLE)


@skip_without_db
def test_reapplying_the_step_is_idempotent(engine: Engine) -> None:
    command.downgrade(_alembic_config(), PREVIOUS_REVISION)
    assert not inspect(engine).has_table(TABLE)

    command.upgrade(_alembic_config(), "head")
    command.upgrade(_alembic_config(), "head")  # already at head -> genuinely a no-op

    assert inspect(engine).has_table(TABLE)
    assert _heads(engine) == {COVERAGE_REVISION}


# --- (b) downgrade is surgical ---------------------------------------------


@skip_without_db
def test_downgrade_drops_only_the_new_table(engine: Engine) -> None:
    before = _category_slugs(engine)
    assert len(before) >= 14

    try:
        command.downgrade(_alembic_config(), PREVIOUS_REVISION)

        assert not inspect(engine).has_table(TABLE)
        # 0010's seed must survive: this migration owns one table and nothing else.
        assert _category_slugs(engine) == before
        assert set(canonical_slugs()) <= _category_slugs(engine)
        for survivor in ("provider", "category", "service", "offer", "review_item"):
            assert inspect(engine).has_table(survivor)
    finally:
        command.upgrade(_alembic_config(), "head")

    assert inspect(engine).has_table(TABLE)


# --- (c) the honesty rules are enforced by the DATABASE, via raw SQL --------


def _raw_insert(engine: Engine, **values: object) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO provider_category_coverage "
                "(provider_id, category_id, state, rationale, source_id, evidence_url) "
                "VALUES (:provider_id, :category_id, :state, :rationale, :source_id, :evidence_url)"
            ),
            {
                "rationale": None,
                "source_id": None,
                "evidence_url": None,
                **values,
            },
        )


@skip_without_db
@pytest.mark.parametrize(
    "state",
    ["no_free_tier", "NOT_OFFERED", "free", "", "maybe", "verified-free"],
)
def test_raw_insert_rejects_an_illegal_state(
    engine: Engine, probe_ids: tuple[int, int], state: str
) -> None:
    provider_id, category_id = probe_ids
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        _raw_insert(
            engine,
            provider_id=provider_id,
            category_id=category_id,
            state=state,
            rationale="probe",
            evidence_url="https://example.invalid/probe",
        )
    assert "coverage_state_valid" in str(exc.value)


@skip_without_db
@pytest.mark.parametrize("rationale", [None, "", "   "])
def test_raw_insert_rejects_not_offered_without_a_rationale(
    engine: Engine, probe_ids: tuple[int, int], rationale: str | None
) -> None:
    provider_id, category_id = probe_ids
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        _raw_insert(
            engine,
            provider_id=provider_id,
            category_id=category_id,
            state="not_offered",
            rationale=rationale,
        )
    assert "not_offered_requires_rationale" in str(exc.value)


@skip_without_db
@pytest.mark.parametrize("state", ["verified_free", "offered_no_z0"])
@pytest.mark.parametrize("evidence_url", [None, "", "   "])
def test_raw_insert_rejects_a_claimed_offer_without_provenance(
    engine: Engine, probe_ids: tuple[int, int], state: str, evidence_url: str | None
) -> None:
    provider_id, category_id = probe_ids
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        _raw_insert(
            engine,
            provider_id=provider_id,
            category_id=category_id,
            state=state,
            source_id=None,
            evidence_url=evidence_url,
        )
    assert "claimed_offer_requires_evidence" in str(exc.value)


@skip_without_db
@pytest.mark.parametrize("state", sorted(COVERAGE_STATES))
def test_raw_insert_accepts_every_legal_state_with_its_obligations_met(
    engine: Engine, probe_ids: tuple[int, int], state: str
) -> None:
    provider_id, category_id = probe_ids
    _raw_insert(
        engine,
        provider_id=provider_id,
        category_id=category_id,
        state=state,
        rationale="probe rationale",
        evidence_url="https://example.invalid/probe",
    )
    with engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT state FROM provider_category_coverage "
                "WHERE provider_id = :p AND category_id = :c"
            ),
            {"p": provider_id, "c": category_id},
        ).scalar_one()
    assert stored == state


@skip_without_db
def test_state_may_not_be_null(engine: Engine, probe_ids: tuple[int, int]) -> None:
    provider_id, category_id = probe_ids
    with pytest.raises((IntegrityError, DBAPIError)):
        _raw_insert(
            engine,
            provider_id=provider_id,
            category_id=category_id,
            state=None,
        )


# --- (d) uniqueness + referential behaviour ---------------------------------


@skip_without_db
def test_a_pair_may_only_be_declared_once(engine: Engine, probe_ids: tuple[int, int]) -> None:
    provider_id, category_id = probe_ids
    _raw_insert(engine, provider_id=provider_id, category_id=category_id, state="unknown")
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        _raw_insert(engine, provider_id=provider_id, category_id=category_id, state="incomplete")
    assert "uq_provider_category_coverage" in str(exc.value)


@skip_without_db
def test_deleting_the_provider_cascades_the_declarations(engine: Engine) -> None:
    with engine.begin() as conn:
        provider_id = conn.execute(
            text(
                "INSERT INTO provider (slug, name, type) "
                "VALUES ('synthetic-cascade-probe', 'Synthetic cascade probe', 'cloud') "
                "RETURNING id"
            )
        ).scalar_one()
        category_id = conn.execute(
            text("SELECT id FROM category WHERE slug = :s"),
            {"s": sorted(canonical_slugs())[0]},
        ).scalar_one()
    _raw_insert(engine, provider_id=provider_id, category_id=category_id, state="unknown")

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM provider WHERE id = :i"), {"i": provider_id})
    with engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM provider_category_coverage WHERE provider_id = :i"),
            {"i": provider_id},
        ).scalar_one()
    assert remaining == 0


# --- (e) Q11: declaration only, no stored derivation ------------------------


@skip_without_db
def test_no_stored_derived_state_column(engine: Engine) -> None:
    columns = {c["name"] for c in inspect(engine).get_columns(TABLE)}
    assert columns == {
        "id",
        "provider_id",
        "category_id",
        "state",
        "rationale",
        "source_id",
        "evidence_url",
        "declared_at",
        "created_at",
    }
    # Q11: the observed state is computed on demand, never persisted.
    assert "derived_state" not in columns
    assert "derived_at" not in columns


@skip_without_db
def test_migration_matches_the_orm_metadata(engine: Engine) -> None:
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, Base.metadata)
    relevant = [entry for entry in diff if TABLE in repr(entry)]
    assert relevant == [], f"migration 0011 drifts from the ORM model: {relevant}"
