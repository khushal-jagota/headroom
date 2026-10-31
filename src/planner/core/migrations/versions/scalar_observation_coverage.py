"""Retain source-authored scalar query facts without altering existing values."""

from alembic import op

revision = "scalar_observation_coverage"
down_revision = "goal_source_responsibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for declaration in ("query_start_at REAL", "query_end_at REAL", "limitation TEXT"):
        op.execute("ALTER TABLE measure_observations ADD COLUMN " + declaration)


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
