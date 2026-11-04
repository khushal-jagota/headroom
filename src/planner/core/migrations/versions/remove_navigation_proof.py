"""Remove the unused screen-update proof records."""

import sqlite3
from typing import cast

from alembic import op

revision = "remove_navigation_proof"
down_revision = "remove_feedback_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from planner.navigation.data import install_source_triggers, remove_source_admission_triggers

    # Remove the retired admission fence before later source rewrites execute.
    remove_source_admission_triggers(
        cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    )
    install_source_triggers(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))
    op.execute("DROP TABLE navigation_commit_witnesses")
    op.execute("DROP TABLE navigation_proof_identity")


def downgrade() -> None:
    raise RuntimeError("Navigation migrations are forward-only")
