"""A Ticket says whether its movement wakes its Sprint Item supervisor.

The wake used to consider every Ticket on an Item. Now it considers only the ones the
user marked when the Ticket was created. The default is no, and there is no backfill:
every Ticket that already exists is unwatched, and the user marks the ones that earn it.

Revision ID: ticket_wakes_supervisor
Revises: sprint_item_supervisor_ping
"""

from __future__ import annotations

from alembic import op

revision = "ticket_wakes_supervisor"
down_revision = "sprint_item_supervisor_ping"
branch_labels = None
depends_on = None

ADD_COLUMN = """
ALTER TABLE tickets ADD COLUMN wakes_supervisor INTEGER NOT NULL DEFAULT 0
"""


def upgrade() -> None:
    op.execute(ADD_COLUMN)


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN wakes_supervisor")
