"""worker queue: job_queue and service_heartbeat tables

Revision ID: 0002_worker_queue
Revises: 0001_scaffold_baseline
Create Date: 2026-07-16

Slice 2 of the F002 application scaffold. Adds the PostgreSQL-backed job queue
infrastructure used by the worker and scheduler services:

- ``job_queue``: pending/running/done/failed jobs claimed with
  ``FOR UPDATE SKIP LOCKED``.
- ``service_heartbeat``: one upserted row per non-HTTP service for
  database-backed liveness health checks.

These are queue/heartbeat *infrastructure* tables, not the catalogue/evidence/Z0
domain model, which is introduced in F003.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_worker_queue"
down_revision: str | None = "0001_scaffold_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name="ck_job_queue_status",
        ),
    )
    # Index that accelerates the worker's claim query (oldest pending first).
    op.create_index(
        "ix_job_queue_pending",
        "job_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "service_heartbeat",
        sa.Column("service", sa.Text(), primary_key=True),
        sa.Column(
            "last_beat_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("detail", sa.Text(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_table("service_heartbeat")
    op.drop_index("ix_job_queue_pending", table_name="job_queue")
    op.drop_table("job_queue")
