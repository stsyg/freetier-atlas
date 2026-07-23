"""source.slug idempotent-sync key (F005 slice 1)

Adds a stable, human-meaningful ``slug`` to the ``source`` table so the
declarative-config -> database sync (``app.ingest.config_sync``) has an
idempotent upsert key: re-running the sync matches an existing source by its
slug instead of creating a duplicate row.

The column is additive and fully reversible:

* ``upgrade`` adds ``source.slug`` (``text``, **nullable**) and a UNIQUE
  constraint ``uq_source_slug``. The column is nullable so pre-existing or
  non-config-managed source rows are unaffected; PostgreSQL treats NULLs as
  distinct, so several unsynced sources may coexist while every config-managed
  source still has a unique slug.
* ``downgrade`` drops exactly that UNIQUE constraint and column and nothing
  else.

This migration touches no other table/column/constraint and installs no
trigger. The ``offer_version`` immutability trigger
(``trg_offer_version_immutable``) and the two 0006 quarantine-separation
triggers (``trg_candidate_official_source``, ``trg_evidence_official_candidate``)
are left completely untouched. The ORM ``Source`` model gains the matching
``slug`` column + ``UniqueConstraint`` so ``compare_metadata`` reports no drift.

Revision ID: 0007_source_slug
Revises: 0006_quarantine_separation
Create Date: 2026-07-27 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_source_slug"
down_revision: str | None = "0006_quarantine_separation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source", sa.Column("slug", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_source_slug", "source", ["slug"])


def downgrade() -> None:
    op.drop_constraint("uq_source_slug", "source", type_="unique")
    op.drop_column("source", "slug")
