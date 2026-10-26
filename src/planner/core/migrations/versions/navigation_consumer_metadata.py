"""Publish canonical ordering, Worker claim, and blocker facts in schema 3."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation.data import upgrade_consumer_metadata

revision = "navigation_consumer_metadata"
down_revision = "reconcile_navigation_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_consumer_metadata(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
