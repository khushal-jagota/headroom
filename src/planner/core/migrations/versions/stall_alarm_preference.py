"""Let the owner store the stall-alarm switch: Panels' own subject and its one type.

notification_preferences names its subjects and types in CHECK constraints, so the
"panels" subject and the "stall_alarm" type need the table rebuilt. Stored switches are
copied unchanged. No row is seeded: the switch ships on, which needs no row.
"""

import sqlite3
from typing import cast

from alembic import op

revision = "stall_alarm_preference"
down_revision = "executor_mailboxes"
branch_labels = None
depends_on = None

NOTIFICATION_PREFERENCES = (
    'CREATE TABLE "notification_preferences" (subject_key TEXT NOT NULL CHECK (subject_key '
    "IN ('tickets','chief_of_staff','managers','goals','panels')),notification_type TEXT "
    "NOT NULL CHECK (notification_type IN ('awaiting_reply','awaiting_answer',"
    "'awaiting_approval','assigned','errored','stall_alarm')),enabled INTEGER NOT NULL "
    "CHECK (enabled IN (0,1)),updated_at INTEGER NOT NULL, "
    "PRIMARY KEY(subject_key, notification_type))"
)


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_preferences'"
        ).fetchone()
        is None
    ):
        return
    conn.execute('ALTER TABLE "notification_preferences" RENAME TO notification_preferences_old')
    conn.execute(NOTIFICATION_PREFERENCES)
    conn.execute(
        "INSERT INTO notification_preferences(subject_key,notification_type,enabled,updated_at) "
        "SELECT subject_key,notification_type,enabled,updated_at FROM notification_preferences_old"
    )
    conn.execute("DROP TABLE notification_preferences_old")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
