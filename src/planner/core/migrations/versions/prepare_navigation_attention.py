"""Prepare attention storage without activating runtime-dependent schema 4."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation.signal_publication import prepare_schema

revision = "prepare_navigation_attention"
down_revision = "navigation_consumer_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    prepare_schema(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
