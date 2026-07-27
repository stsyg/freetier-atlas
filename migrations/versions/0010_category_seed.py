"""category taxonomy seed (F008 slice S1)

Seeds the fourteen canonical categories into the ``category`` table so the
product's fixed evaluation axis exists as **real rows** rather than only as a
code constant.

Source of truth
---------------
``apps/api/app/read_api/taxonomy.py::CATEGORY_TAXONOMY`` (decision D025,
docs/PRODUCT_REQUIREMENTS.md -> "Category taxonomy") is imported directly, so
this seed can never drift from the taxonomy the read API and the adviser match
against. The migration additionally asserts that what it is about to insert is
exactly fourteen rows and exactly the canonical slug set; a mismatch aborts the
upgrade rather than seeding a partial or invented taxonomy.

Idempotency / reversibility
---------------------------
* ``upgrade`` inserts with ``ON CONFLICT (slug) DO NOTHING`` against
  ``uq_category_slug``, so re-running it is a no-op and a category that was
  already present (e.g. inserted by an earlier fixture) is left untouched.
* ``downgrade`` deletes **only** those fourteen slugs -- never ``TRUNCATE``, so
  any non-canonical category a deployment added survives. ``service.category_id``
  is ``ON DELETE SET NULL``, so a downgrade degrades affected services to
  *uncategorised* rather than raising a foreign-key violation or orphaning rows.

This migration is data-only: no table, column, constraint, index or trigger is
created, altered or dropped, so the ORM metadata comparison is unaffected. It
follows ``0009_admin_audit`` so the revision chain stays linear.

Revision ID: 0010_category_seed
Revises: 0009_admin_audit
Create Date: 2026-07-31 00:00:00.000000

"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# ``migrations/env.py`` already puts ``apps/api`` on ``sys.path``; repeat it here
# so this module is importable (and testable) on its own.
_APPS_API = Path(__file__).resolve().parents[2] / "apps" / "api"
if _APPS_API.is_dir() and str(_APPS_API) not in sys.path:  # pragma: no cover - env dependent
    sys.path.insert(0, str(_APPS_API))

from app.read_api.taxonomy import CATEGORY_TAXONOMY, canonical_slugs  # noqa: E402

# revision identifiers, used by Alembic.
revision: str = "0010_category_seed"
down_revision: str | None = "0009_admin_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The product's fixed evaluation axis is exactly fourteen categories.
EXPECTED_CATEGORY_COUNT = 14


def _seed_rows() -> list[dict[str, str]]:
    """The rows to seed, derived from (and validated against) the taxonomy."""

    rows = [{"slug": taxon.slug, "name": taxon.name} for taxon in CATEGORY_TAXONOMY]
    slugs = [row["slug"] for row in rows]

    if len(rows) != EXPECTED_CATEGORY_COUNT:
        raise RuntimeError(
            f"category taxonomy must define exactly {EXPECTED_CATEGORY_COUNT} categories, "
            f"got {len(rows)}: {slugs}"
        )
    if len(set(slugs)) != len(slugs):
        raise RuntimeError(f"category taxonomy contains duplicate slugs: {slugs}")
    if set(slugs) != set(canonical_slugs()):
        raise RuntimeError(
            "seed slug set does not match app.read_api.taxonomy.canonical_slugs(); "
            f"seed={sorted(slugs)} canonical={sorted(canonical_slugs())}"
        )
    return rows


def upgrade() -> None:
    rows = _seed_rows()
    connection = op.get_bind()

    # Slug-keyed and idempotent: a second upgrade inserts nothing.
    connection.execute(
        sa.text(
            "INSERT INTO category (slug, name, description) "
            "VALUES (:slug, :name, NULL) "
            "ON CONFLICT (slug) DO NOTHING"
        ),
        rows,
    )

    # Fail the migration rather than leaving a partially-seeded taxonomy behind.
    present = set(
        connection.execute(
            sa.text("SELECT slug FROM category WHERE slug = ANY(:slugs)"),
            {"slugs": [row["slug"] for row in rows]},
        ).scalars()
    )
    missing = set(canonical_slugs()) - present
    if missing:
        raise RuntimeError(f"category seed incomplete; missing slugs: {sorted(missing)}")


def downgrade() -> None:
    # Delete ONLY the fourteen canonical slugs. Never TRUNCATE: a deployment may
    # hold non-canonical categories this migration did not create.
    op.get_bind().execute(
        sa.text("DELETE FROM category WHERE slug = ANY(:slugs)"),
        {"slugs": list(canonical_slugs())},
    )
