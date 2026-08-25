"""Scratch control: a deliberately failing migration. Delete with the branch.

Isolates the `Apply database migrations` -> `Pytest` edge. Must itself be clean
under `ruff check` and `ruff format --check`, so that the FIRST step to fail in
the `python` job is the migration step and nothing earlier.

Revision ID: 0012_scratch_ci_control
Revises: 0011_provider_category_coverage
"""

from __future__ import annotations

from alembic import op

revision = "0012_scratch_ci_control"
down_revision = "0011_provider_category_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SELECT scratch_ci_control_no_such_function()")


def downgrade() -> None:
    pass
