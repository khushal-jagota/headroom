"""Select a saved checklist as a binary goal's completion input."""

from alembic import op

revision = "primary_goal_checklist"
down_revision = "manual_measures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE goals ADD COLUMN primary_checklist_block_id TEXT")


def downgrade() -> None:
    raise RuntimeError("Primary goal checklists are forward-only. Restore the matching backup.")
