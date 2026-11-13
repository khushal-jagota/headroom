"""Bind retained executor authority to named machines without changing wire IDs."""

import sqlite3
from typing import cast

from alembic import op

revision = "multi_machine_executors"
down_revision = "merge_home_setup_maintenance"
branch_labels = None
depends_on = None

ORIGINAL = "ee17b608-a449-4966-a808-a791ba7df1c7"


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    for table in ("mac_runs", "executor_commands", "executor_child_assignments"):
        orphan = conn.execute(
            f"SELECT 1 FROM {table} r LEFT JOIN executor_incarnations i "
            "ON i.executor_incarnation=r.executor_incarnation "
            "WHERE i.executor_incarnation IS NULL LIMIT 1"
        ).fetchone()
        if orphan:
            raise ValueError(
                f"Cannot bind retained executor authority: orphan in {table}"
            )
    orphan_presence = conn.execute(
        "SELECT 1 FROM mac_executor_presence p LEFT JOIN executor_incarnations i "
        "ON i.executor_incarnation=p.executor_incarnation "
        "WHERE p.executor_incarnation IS NOT NULL AND i.executor_incarnation IS NULL LIMIT 1"
    ).fetchone()
    if orphan_presence:
        raise ValueError("Cannot bind retained executor authority: orphan in presence")
    conn.execute(
        "CREATE INDEX executor_assignments_conversation ON "
        "executor_child_assignments(conversation_id,executor_incarnation)"
    )
    conn.execute(
        f"ALTER TABLE executor_incarnations ADD COLUMN machine_id TEXT NOT NULL "
        f"DEFAULT '{ORIGINAL}' REFERENCES machines(machine_id)"
    )
    conn.execute(
        f"ALTER TABLE executor_commands ADD COLUMN machine_id TEXT NOT NULL "
        f"DEFAULT '{ORIGINAL}' REFERENCES machines(machine_id)"
    )
    conn.execute(
        "CREATE INDEX executor_commands_machine_sequence ON "
        "executor_commands(machine_id,command_sequence)"
    )
    conn.execute(
        "CREATE INDEX executor_commands_machine_open ON "
        "executor_commands(machine_id,executor_incarnation,command_sequence) WHERE state "
        "IN ('waiting','delivered')"
    )
    conn.execute(
        "CREATE INDEX executor_commands_machine_delivered ON "
        "executor_commands(machine_id,command_sequence) WHERE state='delivered'"
    )
    conn.execute(
        "CREATE INDEX executor_commands_machine_waits ON "
        "executor_commands(machine_id,command_sequence) WHERE state IN ('waiting','delivered')"
    )
    conn.execute(
        "CREATE INDEX executor_commands_finished_stop ON "
        "executor_commands(executor_incarnation,child_id,conversation_id) WHERE "
        "operation='stop' AND state='finished'"
    )
    conn.execute(
        f"ALTER TABLE mac_runs ADD COLUMN machine_id TEXT NOT NULL "
        f"DEFAULT '{ORIGINAL}' REFERENCES machines(machine_id)"
    )
    conn.execute("CREATE INDEX mac_runs_machine ON mac_runs(machine_id,conversation_id)")
    conn.execute("CREATE INDEX mac_runs_machine_unresolved ON mac_runs(machine_id,conversation_id) WHERE stop_state<>'closed'")
    conn.execute("CREATE INDEX mac_runs_machine_stopping ON mac_runs(machine_id,executor_incarnation,conversation_id) WHERE stop_state='stopping'")
    conn.execute("ALTER TABLE mac_executor_presence RENAME TO legacy_executor_presence")
    conn.execute(
        "CREATE TABLE mac_executor_presence (machine_id TEXT PRIMARY KEY REFERENCES "
        "machines(machine_id),"
        "executor_incarnation TEXT,identity_json TEXT,last_contact_at_ms INTEGER,"
        "last_lease_at_ms INTEGER,continuous_since_ms INTEGER,going_to_sleep_at_ms INTEGER,"
        "lease_json TEXT,refusal_json TEXT)"
    )
    conn.execute(
        "INSERT INTO mac_executor_presence SELECT ?,executor_incarnation,identity_json,"
        "last_contact_at_ms,last_lease_at_ms,continuous_since_ms,going_to_sleep_at_ms,"
        "lease_json,refusal_json FROM legacy_executor_presence",
        (ORIGINAL,),
    )
    conn.execute("DROP TABLE legacy_executor_presence")
    conn.execute(
        f"ALTER TABLE mac_inbox_positions ADD COLUMN machine_id TEXT NOT NULL "
        f"DEFAULT '{ORIGINAL}' REFERENCES machines(machine_id)"
    )
    conn.execute(
        "CREATE INDEX executor_incarnations_machine ON "
        "executor_incarnations(machine_id,executor_incarnation)"
    )
    # All retained catalogs came from the one original executor. A conflicting legacy
    # catalog key is inconsistent evidence, so SQLite refuses rather than chooses one.
    conn.execute("UPDATE machine_backend_snapshots SET machine_id=?", (ORIGINAL,))


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
