"""scaffold baseline: app_meta table

Revision ID: 0001_scaffold_baseline
Revises:
Create Date: 2026-07-15

This baseline migration proves the migration pipeline works end to end. It
creates a small scaffold ``app_meta`` key/value table and seeds a marker row.
The real domain model (providers, offers, evidence, etc.) is introduced in F003
and will supersede this scaffold table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_scaffold_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_meta",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.bulk_insert(
        sa.table(
            "app_meta",
            sa.column("key", sa.Text()),
            sa.column("value", sa.Text()),
        ),
        [{"key": "scaffold_initialized", "value": "true"}],
    )


def downgrade() -> None:
    op.drop_table("app_meta")
