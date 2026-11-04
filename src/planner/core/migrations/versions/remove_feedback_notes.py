"""Remove the unused product feedback inbox."""

from alembic import op

revision = "remove_feedback_notes"
down_revision = "goal_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE feedback_notes")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
