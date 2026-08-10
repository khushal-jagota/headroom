"""Turn the conversation command list into the typed composer catalog.

Revision ID: conversation_composer_catalog
Revises: weekly_sprint_checkpoint_schedule
"""

from __future__ import annotations

import json

from alembic import op

revision = "conversation_composer_catalog"
down_revision = "weekly_sprint_checkpoint_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    op.execute(
        "ALTER TABLE conversations RENAME COLUMN available_commands TO composer_catalog"
    )
    rows = connection.exec_driver_sql(
        "SELECT conversation_id, composer_catalog FROM conversations"
    ).all()
    for conversation_id, stored in rows:
        commands = json.loads(str(stored))
        catalog = [
            {
                "kind": "command",
                "display_text": f"/{command['name']}",
                "insertion_text": f"/{command['name']} ",
                "description": str(command.get("description", "")),
                "argument_hint": command.get("argument_hint"),
            }
            for command in commands
        ]
        connection.exec_driver_sql(
            "UPDATE conversations SET composer_catalog = ? WHERE conversation_id = ?",
            (json.dumps(catalog, separators=(",", ":"), ensure_ascii=False), conversation_id),
        )


def downgrade() -> None:
    raise NotImplementedError(
        "a typed composer catalog can contain entries that the command list cannot retain"
    )
