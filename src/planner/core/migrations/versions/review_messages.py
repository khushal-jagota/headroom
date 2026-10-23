"""Prepare mixed review projections without granting runtime Signal authority."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation.contracts import PREPARED_SCHEMA_VERSION
from planner.navigation.data import rebuild

revision = "review_messages"
down_revision = "automatic_deployment_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    # Runtime installation owns current attention. Old browser cursors must reset
    # after the review membership and conversation contract change.
    conn.execute("UPDATE navigation_meta SET schema_version=?", (PREPARED_SCHEMA_VERSION,))
    rebuild(conn)


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
