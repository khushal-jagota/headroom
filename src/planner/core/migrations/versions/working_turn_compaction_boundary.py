"""Record the compaction boundary that a working turn produced, apart from Panels' own.

Panels runs its own ``/compact`` after a conversation sits idle, and the Worker was not
part-way through anything then. Only a compaction inside a working turn owes a Worker the
mid-step memory-loss notice, so that boundary gets its own column. Existing rows start at
zero: a boundary that is still unanswered is carried by the Ticket's next wake.
"""

import sqlite3
from typing import cast

from alembic import op

revision = "working_turn_compaction_boundary"
down_revision = "recap_freshness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(
        "ALTER TABLE conversations ADD COLUMN "
        "working_turn_compacted_through_sequence INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
