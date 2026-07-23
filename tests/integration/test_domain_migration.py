"""Integration tests for the F003 domain-model migration (0003).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack
smoke scripts and CI drive this against the live compose Postgres). Covers the
CODEX task 004 Level 2 evaluation surface: migration apply, model/migration
drift, foreign-key and check-constraint enforcement, offer_version immutability,
evidence provenance, representative queries, and a downgrade/re-apply round
trip.

The data-mutating checks run inside a transaction that is rolled back, so the
schema is left clean; offer_version cannot be deleted (it is append-only), which
is exactly why rollback -- not DELETE -- is used for cleanup.
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
from app.models import alembic_include_object, metadata
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]

DOMAIN_TABLES = sorted(metadata.tables.keys())

skip_without_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; start Postgres (scripts/stack-up) and export it to enable.",
)


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
        eng.dispose()


def _seed_graph(conn) -> dict[str, int]:
    """Insert one full Provider -> ... -> Evidence graph; return key ids."""

    ids: dict[str, int] = {}
    ids["provider"] = conn.execute(
        text(
            "INSERT INTO provider (slug, name, type) VALUES ('acme', 'Acme', 'cloud') RETURNING id"
        )
    ).scalar_one()
    ids["service"] = conn.execute(
        text(
            "INSERT INTO service (provider_id, canonical_name, deployment_model) "
            "VALUES (:p, 'widgets', 'managed') RETURNING id"
        ),
        {"p": ids["provider"]},
    ).scalar_one()
    ids["offer"] = conn.execute(
        text(
            "INSERT INTO offer (service_id, offer_type, zero_cost_class) "
            "VALUES (:s, 'always_free', 'Z0_TRUE_FREE') RETURNING id"
        ),
        {"s": ids["service"]},
    ).scalar_one()
    ids["offer_version"] = conn.execute(
        text(
            "INSERT INTO offer_version "
            "(offer_id, version_number, content_hash, offer_type, zero_cost_class) "
            "VALUES (:o, 1, 'h1', 'always_free', 'Z0_TRUE_FREE') RETURNING id"
        ),
        {"o": ids["offer"]},
    ).scalar_one()
    ids["source"] = conn.execute(
        text(
            "INSERT INTO source (adapter_type, trust_level) "
            "VALUES ('html', 'official') RETURNING id"
        )
    ).scalar_one()
    ids["snapshot"] = conn.execute(
        text(
            "INSERT INTO snapshot (source_id, content_location, content_hash) "
            "VALUES (:s, 'blob://x', 'sh1') RETURNING id"
        ),
        {"s": ids["source"]},
    ).scalar_one()
    ids["evidence"] = conn.execute(
        text(
            "INSERT INTO evidence "
            "(source_id, offer_version_id, snapshot_id, content_hash) "
            "VALUES (:src, :ov, :snap, 'eh1') RETURNING id"
        ),
        {"src": ids["source"], "ov": ids["offer_version"], "snap": ids["snapshot"]},
    ).scalar_one()
    return ids


@skip_without_db
def test_all_domain_tables_created(engine: Engine) -> None:
    existing = set(inspect(engine).get_table_names())
    missing = set(DOMAIN_TABLES) - existing
    assert not missing, f"missing domain tables: {sorted(missing)}"


@skip_without_db
def test_no_model_migration_drift(engine: Engine) -> None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "target_metadata": metadata,
                "include_object": alembic_include_object,
                "compare_type": True,
            },
        )
        diffs = compare_metadata(ctx, metadata)
    assert diffs == [], f"unexpected model/migration drift: {diffs}"


@skip_without_db
def test_immutability_trigger_installed(engine: Engine) -> None:
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT tgname FROM pg_trigger WHERE tgname = 'trg_offer_version_immutable'")
        ).scalar_one_or_none()
    assert found == "trg_offer_version_immutable"


@skip_without_db
def test_offer_version_is_append_only(engine: Engine) -> None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            ids = _seed_graph(conn)

            # A second version can be appended.
            conn.execute(
                text(
                    "INSERT INTO offer_version "
                    "(offer_id, version_number, content_hash, offer_type, zero_cost_class) "
                    "VALUES (:o, 2, 'h2', 'always_free', 'Z0_TRUE_FREE')"
                ),
                {"o": ids["offer"]},
            )

            # But an existing version can be neither updated nor deleted.
            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text("UPDATE offer_version SET content_hash = 'x' WHERE id = :i"),
                    {"i": ids["offer_version"]},
                )
            sp.rollback()

            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text("DELETE FROM offer_version WHERE id = :i"),
                    {"i": ids["offer_version"]},
                )
            sp.rollback()
        finally:
            trans.rollback()


@skip_without_db
def test_check_constraints_reject_out_of_vocabulary(engine: Engine) -> None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            ids = _seed_graph(conn)

            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO offer (service_id, offer_type, zero_cost_class) "
                        "VALUES (:s, 'always_free', 'ZX')"
                    ),
                    {"s": ids["service"]},
                )
            sp.rollback()

            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO offer (service_id, offer_type, zero_cost_class) "
                        "VALUES (:s, 'freebie', 'Z0_TRUE_FREE')"
                    ),
                    {"s": ids["service"]},
                )
            sp.rollback()

            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO quota (offer_version_id, metric, exhaustion_behaviour) "
                        "VALUES (:ov, 'requests', 'melts')"
                    ),
                    {"ov": ids["offer_version"]},
                )
            sp.rollback()
        finally:
            trans.rollback()


@skip_without_db
def test_evidence_requires_valid_provenance_fks(engine: Engine) -> None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            _seed_graph(conn)
            sp = conn.begin_nested()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO evidence "
                        "(source_id, offer_version_id, snapshot_id, content_hash) "
                        "VALUES (999999, 999999, 999999, 'z')"
                    )
                )
            sp.rollback()
        finally:
            trans.rollback()


@skip_without_db
def test_representative_provenance_query(engine: Engine) -> None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            ids = _seed_graph(conn)
            row = conn.execute(
                text(
                    "SELECT p.slug, s.canonical_name, o.offer_type, "
                    "       ov.version_number, src.trust_level "
                    "FROM evidence e "
                    "JOIN offer_version ov ON e.offer_version_id = ov.id "
                    "JOIN offer o ON ov.offer_id = o.id "
                    "JOIN service s ON o.service_id = s.id "
                    "JOIN provider p ON s.provider_id = p.id "
                    "JOIN source src ON e.source_id = src.id "
                    "WHERE e.id = :e"
                ),
                {"e": ids["evidence"]},
            ).one()
            assert row.slug == "acme"
            assert row.canonical_name == "widgets"
            assert row.offer_type == "always_free"
            assert row.version_number == 1
            assert row.trust_level == "official"
        finally:
            trans.rollback()


@skip_without_db
def test_migration_round_trip(engine: Engine) -> None:
    cfg = _alembic_config()

    command.downgrade(cfg, "0002_worker_queue")
    after_down = set(inspect(engine).get_table_names())
    assert not (set(DOMAIN_TABLES) & after_down), "domain tables should be gone after downgrade"

    command.upgrade(cfg, "head")
    after_up = set(inspect(engine).get_table_names())
    assert set(DOMAIN_TABLES) <= after_up, "domain tables should be recreated after re-upgrade"

    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "target_metadata": metadata,
                "include_object": alembic_include_object,
                "compare_type": True,
            },
        )
        assert compare_metadata(ctx, metadata) == [], "drift after round trip"


def _source_columns(engine: Engine) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns("source")}


def _source_unique_constraints(engine: Engine) -> set[str]:
    return {uc["name"] for uc in inspect(engine).get_unique_constraints("source")}


def _installed_triggers(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tgname FROM pg_trigger WHERE tgname IN "
                "('trg_offer_version_immutable', 'trg_candidate_official_source', "
                "'trg_evidence_official_candidate')"
            )
        ).scalars()
        return set(rows)


@skip_without_db
def test_source_slug_migration_0007_up_down_up(engine: Engine) -> None:
    """Migration 0007 is additive + reversible with no drift, and it preserves
    the offer_version immutability + both 0006 separation triggers across a full
    up -> down -> up round trip."""

    cfg = _alembic_config()
    all_triggers = {
        "trg_offer_version_immutable",
        "trg_candidate_official_source",
        "trg_evidence_official_candidate",
    }

    # At head (fixture upgraded): slug column + unique constraint present.
    command.upgrade(cfg, "head")
    assert "slug" in _source_columns(engine)
    assert "uq_source_slug" in _source_unique_constraints(engine)
    assert _installed_triggers(engine) == all_triggers

    # Downgrade the single revision: slug + its constraint are removed and
    # nothing else -- every pre-existing trigger survives.
    command.downgrade(cfg, "0006_quarantine_separation")
    assert "slug" not in _source_columns(engine)
    assert "uq_source_slug" not in _source_unique_constraints(engine)
    assert _installed_triggers(engine) == all_triggers

    # Re-upgrade: slug + constraint come back and the ORM reports zero drift.
    command.upgrade(cfg, "head")
    assert "slug" in _source_columns(engine)
    assert "uq_source_slug" in _source_unique_constraints(engine)
    assert _installed_triggers(engine) == all_triggers

    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "target_metadata": metadata,
                "include_object": alembic_include_object,
                "compare_type": True,
            },
        )
        assert compare_metadata(ctx, metadata) == [], "drift after 0007 round trip"
