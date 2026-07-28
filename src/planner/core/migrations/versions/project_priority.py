"""Give each Project an assessed priority or an explicit unassessed state.

Existing Projects stay unassessed. Ordinary creation requires a priority in the
application layer; this nullable column preserves the truthful state for rows that
predate that boundary and for built-in seed rows.

Revision ID: project_priority
Revises: sprint_item_only_placement
"""

from __future__ import annotations

from alembic import op

revision = "project_priority"
down_revision = "sprint_item_only_placement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE projects ADD COLUMN priority TEXT "
        "CHECK (priority IS NULL OR priority IN ('P0','P1','P2','P3'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN priority")
