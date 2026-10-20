"""Install additive diagnostic omission metadata for honest conversation reads."""

import sqlite3
from typing import cast

from alembic import op

from planner.conversation.views.schema import EARLIER_MESSAGES_INDEX_SQL
from planner.maintenance.diagnostic_history import install_expiry_metadata

revision = "diagnostic_expiry_metadata"
down_revision = "open_work_group_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    install_expiry_metadata(conn)
    conn.execute(EARLIER_MESSAGES_INDEX_SQL)


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
