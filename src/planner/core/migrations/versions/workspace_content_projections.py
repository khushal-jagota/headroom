"""Install bounded conversation reads and demand-loaded content catalogs."""

import sqlite3
from typing import cast

from alembic import op

from planner.conversation.history.data import install as install_history
from planner.conversation.views.schema import SCHEMA_STATEMENTS
from planner.conversation.views.writer import backfill_projection
from planner.files.catalog.links import install as install_artifact_links

revision = "workspace_content_projections"
down_revision = "bounded_workspace_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    backfill_projection(conn)
    install_artifact_links(conn)
    install_history(conn)


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
