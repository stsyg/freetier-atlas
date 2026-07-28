"""explicit provider x category coverage state (F008 slice S2)

Creates ``provider_category_coverage``: one row per (provider, canonical
category) pair holding the **declared** coverage state together with its
rationale and provenance.

Why the table exists
--------------------
Before this migration the read API inferred ``not_offered`` from a zero
published-offer count, which conflated "we have not verified this" with "the
provider does not offer it" -- a guess the product's "unknown is better than
guessed" rule forbids. Coverage is now an explicit declaration; ``not_offered``
can only ever be *declared*, never derived.

Declaration only (decision Q11)
-------------------------------
There is deliberately **no** ``derived_state`` / ``derived_at`` column. The
observed state is computed on demand by the pure
``app.read_api.coverage.derive_coverage_state``; persisting a projection of it
would be a second source of truth that can silently go stale -- precisely what
the ``stale`` state exists to detect. A declared-vs-derived contradiction is
recorded durably as an ordinary pending ``review_item``.

Honesty rules live in the DATABASE
----------------------------------
Three CHECK constraints (text shared with the ORM model via
``app.models.vocab``) make a raw ``INSERT`` obey the same rules as the provider
config schema:

* ``ck_provider_category_coverage_coverage_state_valid`` -- the state is one of
  the seven in ``docs/PRODUCT_REQUIREMENTS.md`` -> "Coverage states" plus
  ``unknown``; nothing permissive is accepted.
* ``ck_provider_category_coverage_not_offered_requires_rationale`` --
  ``not_offered`` is a claim, so it must state why (blank is rejected).
* ``ck_provider_category_coverage_claimed_offer_requires_evidence`` --
  ``verified_free`` / ``offered_no_z0`` assert a real offer, so they must carry
  a ``source_id`` or a non-blank ``evidence_url``.

Idempotency / reversibility
---------------------------
* ``upgrade`` is a no-op when the table already exists, so re-running it is
  safe.
* ``downgrade`` drops **only** this table. The fourteen categories seeded by
  ``0010_category_seed`` survive untouched; the FKs are declared here, so no
  other table is altered.

Revision ID: 0011_provider_category_coverage
Revises: 0010_category_seed
Create Date: 2026-08-04 00:00:00.000000

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

from app.models.vocab import (  # noqa: E402
    COVERAGE_EVIDENCE_CHECK,
    COVERAGE_RATIONALE_CHECK,
    COVERAGE_STATE_CHECK,
)

# revision identifiers, used by Alembic.
revision: str = "0011_provider_category_coverage"
down_revision: str | None = "0010_category_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "provider_category_coverage"


def _table_exists() -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(TABLE_NAME)


def upgrade() -> None:
    if _table_exists():  # pragma: no cover - re-apply guard
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("evidence_url", sa.Text(), nullable=True),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            COVERAGE_STATE_CHECK,
            name=op.f("ck_provider_category_coverage_coverage_state_valid"),
        ),
        sa.CheckConstraint(
            COVERAGE_RATIONALE_CHECK,
            name=op.f("ck_provider_category_coverage_not_offered_requires_rationale"),
        ),
        sa.CheckConstraint(
            COVERAGE_EVIDENCE_CHECK,
            name=op.f("ck_provider_category_coverage_claimed_offer_requires_evidence"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider.id"],
            name=op.f("fk_provider_category_coverage_provider_id_provider"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name=op.f("fk_provider_category_coverage_category_id_category"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_provider_category_coverage_source_id_source"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_category_coverage")),
        sa.UniqueConstraint(
            "provider_id",
            "category_id",
            name=op.f("uq_provider_category_coverage"),
        ),
    )


def downgrade() -> None:
    # Drop only this table: 0010's seeded categories are not touched.
    op.drop_table(TABLE_NAME)
