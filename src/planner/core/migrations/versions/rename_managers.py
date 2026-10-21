"""Name the surviving work managers consistently while preserving their identities."""

import sqlite3
from typing import cast

from alembic import op

from planner.core.migrations.manager_rename import migrate

revision = "rename_managers"
down_revision = "remove_calendar_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrate(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def downgrade() -> None:
    raise RuntimeError("Manager rename is forward-only. Restore the matched pre-cutover backup.")
