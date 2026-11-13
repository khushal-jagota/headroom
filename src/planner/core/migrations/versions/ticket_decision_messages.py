"""Keep each decision's instruction until its conversation delivery has a known fate."""

import hashlib
import json
import sqlite3
from typing import cast

from alembic import op

revision = "ticket_decision_messages"
down_revision = "memory_notice_turns_are_maintenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    conn.execute(
        "CREATE TABLE ticket_decision_messages ("
        "decision_id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,"
        "action TEXT NOT NULL,field TEXT NOT NULL,proposal_revision INTEGER,"
        "sender_kind TEXT NOT NULL,sender_id TEXT NOT NULL,request_json TEXT NOT NULL,"
        "message TEXT,model_text TEXT NOT NULL,created_at INTEGER NOT NULL,"
        "conversation_id TEXT,delivery_status TEXT NOT NULL DEFAULT 'pending',"
        "sender_message_id TEXT NOT NULL UNIQUE)"
    )
    conn.execute(
        "CREATE INDEX ticket_decision_messages_pending ON ticket_decision_messages"
        "(ticket_id,created_at) WHERE delivery_status != 'delivered'"
    )

    # The old table held only the last batch, without a proposal identity. Preserve
    # every retained item, including a stage mismatch, and do not invent that identity.
    for row in conn.execute(
        "SELECT ticket_id,stage,feedback_json,revision,created_at FROM ticket_revision_feedback"
    ).fetchall():
        ticket_id, stage, feedback_json, version, created_at = row
        for index, item in enumerate(json.loads(feedback_json)):
            identity = f"legacy-feedback:{ticket_id}:{version}:{index}"
            kind, sender_id, message = item["sender_kind"], item["sender_id"], item["message"]
            actor = {
                "owner": "User",
                "chief": "Chief",
                "manager": "Manager",
                "ticket": "Worker",
            }.get(kind, kind)
            if kind == "manager":
                manager = conn.execute(
                    "SELECT title FROM managers WHERE id=?", (sender_id,)
                ).fetchone()
                if manager is not None:
                    actor += f" {manager[0]}"
            text = f"Legacy revision feedback from {actor} for stage {stage}:\n{message}"
            conn.execute(
                "INSERT INTO ticket_decision_messages "
                "(decision_id,ticket_id,action,field,sender_kind,sender_id,request_json,message,"
                "model_text,created_at,sender_message_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    ticket_id,
                    "legacy feedback",
                    stage,
                    kind,
                    sender_id,
                    "{}",
                    message,
                    text,
                    created_at,
                    "ticket_decision_" + hashlib.sha256(identity.encode()).hexdigest(),
                ),
            )
    conn.execute("DROP TABLE ticket_revision_feedback")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only. Restore the matching backup.")
