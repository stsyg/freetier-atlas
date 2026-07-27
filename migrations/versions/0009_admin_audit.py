"""admin audit log (F007 slice 4)

Adds the single append-only ``admin_audit`` table that records every
private-admin authentication attempt (success and denial) and every admin
action (AI kill-switch toggle, review-queue disposition, source-health and
config-diff views).

Security: an audit row NEVER stores a secret -- no OAuth client secret, no
access token, no cookie signing key, and no raw ``code``/``state`` value. It
records only the public GitHub login (or NULL when identity was never
established), a stable action code, an outcome, an optional denial reason, and a
small non-secret JSON context.

The migration is purely additive and fully reversible:

* ``upgrade`` creates ``admin_audit``. No CHECK constraints or foreign keys are
  used (valid enum-like values are enforced in application code) so the ORM
  metadata and this migration compare cleanly with ``compare_type=True``.
* ``downgrade`` drops exactly that one table and nothing else, giving a clean
  up -> down -> up round-trip with no drift.

No other table/column/constraint/trigger is touched. This migration follows
``0008_adviser_abuse_controls`` so the revision chain stays linear.

Revision ID: 0009_admin_audit
Revises: 0008_adviser_abuse_controls
Create Date: 2026-07-29 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_admin_audit"
down_revision: str | None = "0008_adviser_abuse_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit")),
    )


def downgrade() -> None:
    op.drop_table("admin_audit")
