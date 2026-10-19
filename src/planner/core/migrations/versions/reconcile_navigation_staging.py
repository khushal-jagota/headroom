"""Join accepted staging source changes with the local navigation projection."""

import sqlite3
from typing import cast

from alembic import op

from planner.navigation.data import rebuild

revision = "reconcile_navigation_staging"
down_revision = ("navigation_delivery_proof", "remove_ticket_retirement")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Source rewrites on the other branch can precede or follow navigation backfill.
    # Rebuild once both histories exist, with the canonical epoch/journal boundary.
    rebuild(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
