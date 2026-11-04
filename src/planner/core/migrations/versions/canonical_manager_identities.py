"""Finish the Manager rename without changing retained conversations or authored history."""

import json
import sqlite3
from typing import cast

from alembic import op

revision = "canonical_manager_identities"
down_revision = "remove_navigation_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrate(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def migrate(conn: sqlite3.Connection) -> None:
    # Offline migrations have no producer lease or runtime SQL functions. Reinstall
    # canonical capture declarations after the retained source rows cross the rename.
    source_triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'signal_source_%'"
    ).fetchall()
    for (name,) in source_triggers:
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    for row in conn.execute("SELECT agent_key,conversation_id FROM agents").fetchall():
        key, conversation_id = row
        if not key.startswith("sprint_item_supervisor_"):
            continue
        canonical = "manager_" + key.removeprefix("sprint_item_supervisor_")
        conn.execute(
            "INSERT INTO agents(agent_key,conversation_id) VALUES(?,?) "
            "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
            (canonical, conversation_id),
        )
        conn.execute("DELETE FROM agents WHERE agent_key=?", (key,))
    for row in conn.execute(
        "SELECT conversation_id,identity_environment_variables,role_text FROM conversations"
    ).fetchall():
        conversation_id, identity_json, role_text = row
        pairs = json.loads(identity_json)
        values = dict(pairs)
        if values.get("PLAN_ACTOR") not in {"sprint_item_supervisor", "manager"}:
            continue
        manager_id = values.get("PLAN_MANAGER_ID") or values.get("PLAN_SPRINT_ITEM_ID")
        canonical_pairs = [
            [key, value]
            for key, value in pairs
            if key not in {"PLAN_ACTOR", "PLAN_MANAGER_ID", "PLAN_SPRINT_ITEM_ID"}
        ]
        canonical_pairs.extend([["PLAN_ACTOR", "manager"], ["PLAN_MANAGER_ID", manager_id]])
        conn.execute(
            "UPDATE conversations SET identity_environment_variables=?,role_text=? "
            "WHERE conversation_id=?",
            (
                json.dumps(canonical_pairs),
                role_text.replace("You are a Sprint Item supervisor.", "You are a Manager."),
                conversation_id,
            ),
        )
    for ticket_id, approvals_json in conn.execute(
        "SELECT id,field_approvals FROM tickets"
    ).fetchall():
        approvals = json.loads(approvals_json)
        changed = False
        for approval in approvals.values():
            principal = approval.get("principal")
            if principal and principal.get("kind") == "sprint_item":
                principal["kind"] = "manager"
                changed = True
        if changed:
            conn.execute(
                "UPDATE tickets SET field_approvals=? WHERE id=?",
                (json.dumps(approvals), ticket_id),
            )
    from planner.navigation.data import rebuild

    previous_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        rebuild(conn)
    finally:
        conn.row_factory = previous_factory

    from planner.conversation.publication.persistence import install_migration_source_triggers

    install_migration_source_triggers(conn)


def downgrade() -> None:
    raise RuntimeError("Manager identities are forward-only. Restore the matching backup.")
