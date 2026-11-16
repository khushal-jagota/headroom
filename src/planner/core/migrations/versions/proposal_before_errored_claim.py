"""Refresh the navigation projection for parked proposals with errored claims."""

import sqlite3
from typing import cast

from alembic import op

revision = "proposal_before_errored_claim"
down_revision = "consequences_without_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from planner.navigation.data import rebuild

    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    previous_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rebuild(conn)
    finally:
        conn.row_factory = previous_factory


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
