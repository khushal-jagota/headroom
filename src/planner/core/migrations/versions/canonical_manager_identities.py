"""Finish the Manager rename without changing retained conversations or authored history."""

import json
import sqlite3
from typing import cast

from alembic import op

from planner.runtime.logic.conversation_start_resolution import MANAGER_ROLE_TEXT

revision = "canonical_manager_identities"
down_revision = "goal_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrate(cast(sqlite3.Connection, op.get_bind().connection.driver_connection))


def migrate(conn: sqlite3.Connection) -> None:
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
        "SELECT conversation_id,identity_environment_variables FROM conversations"
    ).fetchall():
        conversation_id, identity_json = row
        pairs = json.loads(identity_json)
        values = dict(pairs)
        if values.get("PLAN_ACTOR") not in {"sprint_item_supervisor", "manager"}:
            continue
        manager_id = values.get("PLAN_MANAGER_ID") or values.get("PLAN_SPRINT_ITEM_ID")
        canonical_pairs = [
            [key, value] for key, value in pairs
            if key not in {"PLAN_ACTOR", "PLAN_MANAGER_ID", "PLAN_SPRINT_ITEM_ID"}
        ]
        canonical_pairs.extend([["PLAN_ACTOR", "manager"], ["PLAN_MANAGER_ID", manager_id]])
        conn.execute(
            "UPDATE conversations SET identity_environment_variables=?,role_text=? "
            "WHERE conversation_id=?",
            (json.dumps(canonical_pairs), MANAGER_ROLE_TEXT, conversation_id),
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
    conn.execute(
        "UPDATE signal_publication_associations SET value="
        "json_set(value,'$.owner_kind','manager') "
        "WHERE json_extract(value,'$.owner_kind')='orchestrator'"
    )
    from planner.navigation.data import rebuild

    rebuild(conn)


def downgrade() -> None:
    raise RuntimeError("Manager identities are forward-only. Restore the matching backup.")
