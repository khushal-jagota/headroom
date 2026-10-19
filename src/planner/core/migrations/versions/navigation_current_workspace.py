"""Join current staging source facts with navigation without runtime activation."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation.data import upgrade_current_workspace

revision = "navigation_current_workspace"
down_revision = ("prepare_navigation_attention", "remove_priority")
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_current_workspace(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
