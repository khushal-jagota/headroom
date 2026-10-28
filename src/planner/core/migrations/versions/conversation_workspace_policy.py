"""Persist workspace interpretation with the conversation that chose it."""

from alembic import op

revision = "conversation_workspace_policy"
down_revision = "merge_resident_native_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE conversations ADD COLUMN workspace_path_policy TEXT NOT NULL "
        "DEFAULT 'legacy_home_mapping' "
        "CHECK (workspace_path_policy IN ('legacy_home_mapping', 'native_absolute'))"
    )


def downgrade() -> None:
    raise RuntimeError("Conversation workspace policy is forward-only")
