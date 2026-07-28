"""Integration tests for the F007 slice-4 admin audit migration (0009).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL (the stack
smoke scripts drive this against the live compose Postgres). Covers the Level-2
surface for this slice: migration 0009 applies additively on top of 0008, the
ORM model and migration are drift-free, the up -> down -> up round-trip is clean
and leaves the head at ``0009_admin_audit``, and the PostgreSQL audit store
appends rows while stripping any secret-looking context key (defence in depth).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.admin.audit import PostgresAdminAuditStore
from app.models import alembic_include_object, metadata
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

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


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    command.upgrade(_alembic_config(), "head")
    eng = create_engine(DATABASE_URL)
    try:
        yield eng
    finally:
        eng.dispose()


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


@skip_without_db
def test_admin_audit_migration_0009_up_down_up(engine: Engine) -> None:
    """0009 is additive + reversible with no drift across a full round trip."""

    cfg = _alembic_config()

    # At head (fixture upgraded) the admin_audit table exists. The head revision
    # itself moves on as later migrations land (0010 seeds the category
    # taxonomy), so this asserts the 0009 STEP round-trips, not the head id.
    command.upgrade(cfg, "head")
    assert "admin_audit" in inspect(engine).get_table_names()
    head_revision = _current_revision(engine)

    # Downgrade past 0009: admin_audit is dropped and the head falls to 0008.
    command.downgrade(cfg, "0008_adviser_abuse_controls")
    assert "admin_audit" not in inspect(engine).get_table_names()
    assert _current_revision(engine) == "0008_adviser_abuse_controls"

    # Step forward onto 0009 exactly, then on to head again.
    command.upgrade(cfg, "0009_admin_audit")
    assert "admin_audit" in inspect(engine).get_table_names()
    assert _current_revision(engine) == "0009_admin_audit"

    command.upgrade(cfg, "head")
    assert "admin_audit" in inspect(engine).get_table_names()
    assert _current_revision(engine) == head_revision

    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "target_metadata": metadata,
                "include_object": alembic_include_object,
                "compare_type": True,
            },
        )
        assert compare_metadata(ctx, metadata) == [], "drift after 0009 round trip"


@skip_without_db
def test_admin_audit_store_appends_and_strips_secrets(engine: Engine) -> None:
    """The Postgres audit store persists rows and never writes a secret key."""

    marker = "itest-admin-audit-0009"
    store = PostgresAdminAuditStore(engine)
    now = datetime.now(UTC)
    with engine.connect() as conn:
        baseline = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM admin_audit")).scalar_one()
    try:
        store.record(
            actor=marker,
            action="kill_switch_toggle",
            outcome="success",
            reason=None,
            context={"enabled": True, "access_token": "leaked", "code": "abc"},
            now=now,
        )
        store.record(
            actor=None,
            action="login",
            outcome="denied",
            reason="not_allowlisted",
            context=None,
            now=now,
        )

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT actor, action, outcome, reason, context "
                    "FROM admin_audit WHERE id > :b ORDER BY id"
                ),
                {"b": baseline},
            ).all()

        assert len(rows) == 2
        success = rows[0]
        assert success.action == "kill_switch_toggle" and success.outcome == "success"
        # Secret-looking keys are stripped; the safe key survives.
        assert success.context == {"enabled": True}
        denied = rows[1]
        assert denied.actor is None and denied.reason == "not_allowlisted"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM admin_audit WHERE id > :b"), {"b": baseline})
