"""Give each Project an optional explicit folder path.

Revision ID: project_folder_path
Revises: ticket_review_routes
"""

from __future__ import annotations

from alembic import op

revision = "project_folder_path"
down_revision = "ticket_review_routes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN folder_path TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE projects DROP COLUMN folder_path")
