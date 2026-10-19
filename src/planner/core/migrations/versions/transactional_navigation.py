"""Backfill generic navigation and its transaction-owned journal."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation import create_schema

revision = "transactional_navigation"
down_revision = "durable_conversation_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_schema(
        cast(sqlite3.Connection, op.get_bind().connection.driver_connection), schema_version=1
    )


def downgrade() -> None:
    raise RuntimeError("Navigation migration is forward-only")
