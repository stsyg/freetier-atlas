"""ingest candidates (F004 slice 2)

Adds the pre-publication ingestion persistence layer:

* ``candidate`` - normalised, hashed observations produced by a ScanRun. May be
  linked to a service/offer once matched, but never mutates ``offer_version``.
* ``discovery_candidate`` - a community-provenance quarantine table. Rows here
  are never linked to ``evidence`` or ``offer_version``.
* ``evidence`` gains an optional ``candidate_id`` link so official sources can
  attach provenance to a candidate before any publication decision. The
  ``offer_version_id`` link becomes optional, guarded by a CHECK constraint that
  requires at least one linkage target.

Reversible: downgrade drops the new tables/columns and restores the original
``evidence.offer_version_id NOT NULL`` shape. The ``offer_version``
immutability trigger is untouched by this migration.

Revision ID: 0004_ingest_candidates
Revises: 0003_domain_model
Create Date: 2026-07-21 16:37:07.556907

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_ingest_candidates"
down_revision: str | None = "0003_domain_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_candidate",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("licence", sa.Text(), nullable=True),
        sa.Column(
            "discovery_date",
            sa.Date(),
            server_default=sa.text("CURRENT_DATE"),
            nullable=False,
        ),
        sa.Column("import_method", sa.Text(), nullable=False),
        sa.Column(
            "verification_status",
            sa.Text(),
            server_default=sa.text("'unverified'"),
            nullable=False,
        ),
        sa.Column("candidate_name", sa.Text(), nullable=True),
        sa.Column("official_url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "import_method IN ('manual', 'community_import', 'automated')",
            name=op.f("ck_discovery_candidate_import_method_valid"),
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'verifying', 'verified', 'rejected')",
            name=op.f("ck_discovery_candidate_verification_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_discovery_candidate_source_id_source"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_candidate")),
    )
    op.create_table(
        "candidate",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scan_run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=True),
        sa.Column("offer_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "verification_state",
            sa.Text(),
            server_default=sa.text("'candidate'"),
            nullable=False,
        ),
        sa.Column(
            "candidate_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("candidate_key", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "official",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verification_state IN ('detected', 'extracting', 'candidate', "
            "'verified', 'verified_with_caveats', 'conflict', 'stale', "
            "'withdrawn', 'rejected')",
            name=op.f("ck_candidate_verification_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offer.id"],
            name=op.f("fk_candidate_offer_id_offer"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id"],
            ["scan_run.id"],
            name=op.f("fk_candidate_scan_run_id_scan_run"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["service.id"],
            name=op.f("fk_candidate_service_id_service"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_candidate_source_id_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate")),
    )
    op.add_column("evidence", sa.Column("candidate_id", sa.BigInteger(), nullable=True))
    op.alter_column(
        "evidence",
        "offer_version_id",
        existing_type=sa.BIGINT(),
        nullable=True,
    )
    op.create_foreign_key(
        op.f("fk_evidence_candidate_id_candidate"),
        "evidence",
        "candidate",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_evidence_evidence_link_target"),
        "evidence",
        "offer_version_id IS NOT NULL OR candidate_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_evidence_evidence_link_target"), "evidence", type_="check")
    op.drop_constraint(op.f("fk_evidence_candidate_id_candidate"), "evidence", type_="foreignkey")
    op.alter_column(
        "evidence",
        "offer_version_id",
        existing_type=sa.BIGINT(),
        nullable=False,
    )
    op.drop_column("evidence", "candidate_id")
    op.drop_table("candidate")
    op.drop_table("discovery_candidate")
