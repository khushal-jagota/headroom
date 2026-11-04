"""Record goal completion evidence and its date without inventing legacy history."""

from alembic import op

revision = "goal_outcomes"
down_revision = "manager_launch_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE goals ADD COLUMN completion_rule TEXT")
    op.execute("ALTER TABLE goals ADD COLUMN completion_date TEXT")


def downgrade() -> None:
    raise RuntimeError("Goal outcomes are forward-only. Restore the matching backup.")
