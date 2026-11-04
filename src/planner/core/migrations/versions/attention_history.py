"""Retain historical owner approval events and gate intervals."""

import sqlite3
import time
from typing import cast

from alembic import op

from planner.attention.history import install_history

revision = "attention_history"
down_revision = "manager_launch_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    install_history(conn, started_at=int(time.time()))


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
