"""Record each conversation's live background tasks so a busy session is not stopped."""

from __future__ import annotations

from alembic import op

revision = "conversation_background_tasks"
down_revision = "ticket_owned_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "CREATE TABLE conversation_background_tasks ("
        "conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),"
        "task_id TEXT NOT NULL, task_description TEXT NOT NULL,"
        "PRIMARY KEY(conversation_id, task_id))"
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE conversation_background_tasks")
