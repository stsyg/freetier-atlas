"""public-adviser abuse controls (F007 slice 2)

Adds the five operational tables that back the public-adviser abuse layer:
per-IP rate limiting, request dedupe, the AI kill switch, per-provider circuit
breakers, and self-hosted proof-of-work challenge tracking. These protect
``/adviser/recommend``, ``/adviser/recommend/assisted`` and ``/adviser/export``.

Privacy: a raw client IP is **never** stored -- only an HMAC-SHA256 hash keyed
by a server secret. Request bodies are never stored -- only an HMAC digest for
dedupe. Consent assertions and free-text descriptions are never persisted.

The migration is purely additive and fully reversible:

* ``upgrade`` creates ``rate_limit_bucket``, ``abuse_flag``, ``circuit_breaker``,
  ``request_dedupe`` and ``pow_challenge``. No CHECK constraints or foreign keys
  are used (valid enum-like values are enforced in application code) so the ORM
  metadata and this migration compare cleanly with ``compare_type=True``.
* ``downgrade`` drops exactly those five tables and nothing else, giving a clean
  up -> down -> up round-trip with no drift.

No other table/column/constraint/trigger is touched. This migration follows
``0007_source_slug`` so the revision chain stays linear.

Revision ID: 0008_adviser_abuse_controls
Revises: 0007_source_slug
Create Date: 2026-07-28 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_adviser_abuse_controls"
down_revision: str | None = "0007_source_slug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_bucket",
        sa.Column("ip_hash", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("window_key", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "ip_hash",
            "scope",
            "window_key",
            name=op.f("pk_rate_limit_bucket"),
        ),
    )
    op.create_table(
        "abuse_flag",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_abuse_flag")),
    )
    op.create_table(
        "circuit_breaker",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("provider", name=op.f("pk_circuit_breaker")),
    )
    op.create_table(
        "request_dedupe",
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("dedupe_key", name=op.f("pk_request_dedupe")),
    )
    op.create_table(
        "pow_challenge",
        sa.Column("challenge_id", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("ip_hash", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("solved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("challenge_id", name=op.f("pk_pow_challenge")),
    )


def downgrade() -> None:
    op.drop_table("pow_challenge")
    op.drop_table("request_dedupe")
    op.drop_table("circuit_breaker")
    op.drop_table("abuse_flag")
    op.drop_table("rate_limit_bucket")
