"""Integration tests for migration ``0010_category_seed`` (F008 slice S1).

Skipped unless ``DATABASE_URL`` points at a reachable PostgreSQL. These prove
that the fourteen canonical product categories are REAL ROWS derived from the
single source of truth (``app.read_api.taxonomy.CATEGORY_TAXONOMY``) and that
the seed is safe to apply and to roll back:

(a) ``alembic upgrade head`` reaches ``0010_category_seed`` and the seeded slug
    set is EXACTLY ``taxonomy.canonical_slugs()`` (14 rows, no extras);
(b) re-running the upgrade step is a no-op (idempotent ``ON CONFLICT``);
(c) ``downgrade 0009_admin_audit`` removes exactly those fourteen slugs, never
    truncating the table -- an operator-authored category survives; and
(d) because ``service.category_id`` is ``ON DELETE SET NULL``, downgrading with
    a categorised service in place degrades it to uncategorised instead of
    raising an FK violation, and the service row itself survives.

The upgrade/downgrade steps run OUTSIDE the rolled-back test transaction (alembic
owns its own connection), so each test restores the database to ``head`` before
returning.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models.domain import Provider, Service
from app.read_api.taxonomy import CATEGORY_TAXONOMY, canonical_slugs
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

SEED_REVISION = "0010_category_seed"
PREVIOUS_REVISION = "0009_admin_audit"


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
        # Always hand the database back at head, whatever a test did to it.
        command.upgrade(_alembic_config(), "head")
        eng.dispose()


def _slugs(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT slug FROM category")).scalars())


def _alembic_heads(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())


# --- (a) upgrade seeds exactly the canonical fourteen -----------------------


@skip_without_db
def test_upgrade_head_seeds_exactly_the_canonical_categories(engine: Engine) -> None:
    assert SEED_REVISION in _alembic_heads(engine)

    expected = set(canonical_slugs())
    assert len(expected) == 14
    assert len(CATEGORY_TAXONOMY) == 14

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT slug, name FROM category ORDER BY slug")).all()

    seeded = {slug for slug, _ in rows}
    # Equality, not containment: no extra and no missing canonical slug.
    assert seeded == expected
    assert len(rows) == 14

    names = {slug: name for slug, name in rows}
    for taxon in CATEGORY_TAXONOMY:
        assert names[taxon.slug] == taxon.name


# --- (b) idempotency --------------------------------------------------------


@skip_without_db
def test_second_upgrade_is_a_no_op(engine: Engine) -> None:
    before = _slugs(engine)
    with engine.connect() as conn:
        count_before = conn.execute(text("SELECT count(*) FROM category")).scalar_one()

    # Re-apply the seed step itself: downgrade one revision then upgrade again.
    command.downgrade(_alembic_config(), PREVIOUS_REVISION)
    command.upgrade(_alembic_config(), "head")
    command.upgrade(_alembic_config(), "head")  # already at head -> genuinely a no-op

    with engine.connect() as conn:
        count_after = conn.execute(text("SELECT count(*) FROM category")).scalar_one()

    assert _slugs(engine) == before
    assert count_after == count_before == 14


# --- (c) downgrade is surgical, never a truncate ---------------------------


@skip_without_db
def test_downgrade_removes_only_the_seeded_slugs(engine: Engine) -> None:
    # An operator-authored, non-canonical category must survive the downgrade.
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO category (slug, name) VALUES (:s, :n) ON CONFLICT DO NOTHING"),
            {"s": "operator-authored-not-canonical", "n": "Operator authored"},
        )

    try:
        command.downgrade(_alembic_config(), PREVIOUS_REVISION)

        remaining = _slugs(engine)
        assert remaining & set(canonical_slugs()) == set()
        assert "operator-authored-not-canonical" in remaining
    finally:
        command.upgrade(_alembic_config(), "head")
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM category WHERE slug = :s"),
                {"s": "operator-authored-not-canonical"},
            )

    assert _slugs(engine) == set(canonical_slugs())


# --- (d) downgrade degrades categorised services to NULL -------------------


@skip_without_db
def test_downgrade_sets_service_category_null_and_keeps_the_service(engine: Engine) -> None:
    slug = next(iter(sorted(set(canonical_slugs()))))

    with Session(engine) as sess:
        provider = Provider(
            slug="synthetic-downgrade-probe", name="Synthetic downgrade probe", type="cloud"
        )
        sess.add(provider)
        sess.flush()
        category_id = sess.execute(
            text("SELECT id FROM category WHERE slug = :s"), {"s": slug}
        ).scalar_one()
        service = Service(
            provider_id=provider.id,
            category_id=category_id,
            canonical_name="Synthetic downgrade probe service",
            deployment_model="managed",
        )
        sess.add(service)
        sess.commit()
        service_id = service.id
        provider_id = provider.id

    try:
        # ON DELETE SET NULL: this must NOT raise an FK violation.
        command.downgrade(_alembic_config(), PREVIOUS_REVISION)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, category_id FROM service WHERE id = :i"), {"i": service_id}
            ).one_or_none()
        assert row is not None, "downgrade must degrade the service, not delete it"
        assert row.category_id is None
    finally:
        command.upgrade(_alembic_config(), "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM service WHERE id = :i"), {"i": service_id})
            conn.execute(text("DELETE FROM provider WHERE id = :i"), {"i": provider_id})

    assert _slugs(engine) == set(canonical_slugs())
