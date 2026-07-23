"""quarantine separation hardening (F004 slice 6)

Database-level defense-in-depth for the community/official trust boundary
(docs/SOURCE_REUSE_AND_PROVENANCE.md). The application layer
(``app.ingest.scan`` + ``app.ingest.trust``) already routes official sources to
``evidence`` and community sources to the quarantined ``discovery_candidate``
table, but nothing at the database stopped raw SQL from crossing the boundary.
This migration installs two ``BEFORE INSERT OR UPDATE`` triggers so the invariant
holds even against direct SQL:

* ``trg_candidate_official_source`` -- a ``candidate`` may be marked
  ``official = true`` only when its ``source.trust_level = 'official'``. A
  community/unverified source can therefore never own an official candidate, so
  a quarantined discovery can never be relabelled into the official pipeline from
  community data alone.
* ``trg_evidence_official_candidate`` -- an ``evidence`` row whose
  ``candidate_id`` is set may reference only an *official* candidate. Combined
  with the first trigger, community-sourced candidates (forced ``official=false``)
  can never acquire evidence.

Together with the pre-existing structural isolation (``discovery_candidate`` has
no foreign key into ``evidence`` or ``offer_version``) this makes
"community sources cannot become verified evidence" an enforced invariant.

Both triggers raise with ``ERRCODE = 'restrict_violation'`` (SQLSTATE class 23),
matching the ``offer_version`` immutability trigger so violations surface as an
``IntegrityError``.

Reversible: ``downgrade`` drops exactly the two triggers and their functions and
nothing else. This migration adds no table/column/constraint, so the ORM
metadata is unchanged and ``compare_metadata`` reports no drift. The
``offer_version`` immutability trigger and every 0001-0005 object are untouched.

Revision ID: 0006_quarantine_separation
Revises: 0005_change_event_candidate_link
Create Date: 2026-07-23 13:45:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_quarantine_separation"
down_revision: str | None = "0005_change_event_candidate_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Trigger A: a candidate may be flagged official only if its source is official.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION candidate_official_requires_official_source()
        RETURNS trigger AS $$
        DECLARE
            src_trust text;
        BEGIN
            IF NEW.official THEN
                SELECT trust_level INTO src_trust FROM source WHERE id = NEW.source_id;
                IF src_trust IS DISTINCT FROM 'official' THEN
                    RAISE EXCEPTION
                        'candidate.official may be true only for an official source '
                        '(source % has trust_level %); community/quarantined candidates '
                        'must remain non-official',
                        NEW.source_id, coalesce(src_trust, 'NULL')
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_candidate_official_source
        BEFORE INSERT OR UPDATE ON candidate
        FOR EACH ROW EXECUTE FUNCTION candidate_official_requires_official_source();
        """
    )

    # Trigger B: candidate-linked evidence may reference only an official candidate.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION evidence_requires_official_candidate()
        RETURNS trigger AS $$
        DECLARE
            cand_official boolean;
        BEGIN
            IF NEW.candidate_id IS NOT NULL THEN
                SELECT official INTO cand_official FROM candidate WHERE id = NEW.candidate_id;
                IF cand_official IS DISTINCT FROM true THEN
                    RAISE EXCEPTION
                        'evidence may reference only an official candidate '
                        '(candidate % is not official); community-sourced discovery '
                        'can never become verified evidence',
                        NEW.candidate_id
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidence_official_candidate
        BEFORE INSERT OR UPDATE ON evidence
        FOR EACH ROW EXECUTE FUNCTION evidence_requires_official_candidate();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_evidence_official_candidate ON evidence;")
    op.execute("DROP FUNCTION IF EXISTS evidence_requires_official_candidate();")
    op.execute("DROP TRIGGER IF EXISTS trg_candidate_official_source ON candidate;")
    op.execute("DROP FUNCTION IF EXISTS candidate_official_requires_official_source();")
