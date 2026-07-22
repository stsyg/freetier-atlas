"""change_event candidate linkage (F004 slice 3)

Extends ``change_event`` so the reconciliation pass can record *pre-publication*
candidate diffs as DRAFT change events, without any publication path:

* adds ``previous_candidate_id`` and ``new_candidate_id`` foreign keys to
  ``candidate`` (``ondelete=SET NULL``);
* relaxes ``offer_id`` to nullable (a candidate-diff change event has no offer);
* adds a CHECK (``ck_change_event_change_link_target``) requiring at least one
  linkage target so a change event is never orphaned.

Reversible: downgrade drops the CHECK, the two candidate FKs/columns, and
restores ``offer_id NOT NULL``. The ``offer_version`` immutability trigger and
every 0001-0004 table are untouched by this migration.

Revision ID: 0005_change_event_candidate_link
Revises: 0004_ingest_candidates
Create Date: 2026-07-22 12:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_change_event_candidate_link"
down_revision: str | None = "0004_ingest_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "change_event", sa.Column("previous_candidate_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("change_event", sa.Column("new_candidate_id", sa.BigInteger(), nullable=True))
    op.alter_column(
        "change_event",
        "offer_id",
        existing_type=sa.BIGINT(),
        nullable=True,
    )
    op.create_foreign_key(
        op.f("fk_change_event_previous_candidate_id_candidate"),
        "change_event",
        "candidate",
        ["previous_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_change_event_new_candidate_id_candidate"),
        "change_event",
        "candidate",
        ["new_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_change_event_change_link_target"),
        "change_event",
        "offer_id IS NOT NULL OR previous_candidate_id IS NOT NULL OR new_candidate_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_change_event_change_link_target"), "change_event", type_="check")
    op.drop_constraint(
        op.f("fk_change_event_new_candidate_id_candidate"),
        "change_event",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_change_event_previous_candidate_id_candidate"),
        "change_event",
        type_="foreignkey",
    )
    op.alter_column(
        "change_event",
        "offer_id",
        existing_type=sa.BIGINT(),
        nullable=False,
    )
    op.drop_column("change_event", "new_candidate_id")
    op.drop_column("change_event", "previous_candidate_id")
