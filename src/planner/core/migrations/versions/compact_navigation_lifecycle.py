"""Replace lifecycle history in navigation with compact raw source facts."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation.data import upgrade_compact_lifecycle

revision = "compact_navigation_lifecycle"
down_revision = "transactional_navigation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_compact_lifecycle(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
