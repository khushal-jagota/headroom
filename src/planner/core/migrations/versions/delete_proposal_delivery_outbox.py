"""Delete the proposal delivery outbox and its failure events.

Revision ID: delete_proposal_delivery_outbox
Revises: proposal_delivery_failures
"""

from __future__ import annotations

import json
from typing import TypedDict

import sqlalchemy as sa
from alembic import op

revision = "delete_proposal_delivery_outbox"
down_revision = "proposal_delivery_failures"
branch_labels = None
depends_on = None


class _FeedbackBatch(TypedDict):
    stage: str
    items: list[dict[str, str]]
    created_at: int
    updated_at: int


def upgrade() -> None:
    op.execute(
        "CREATE TABLE ticket_revision_feedback ("
        "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,"
        "stage TEXT NOT NULL CHECK (length(trim(stage)) > 0),"
        "feedback_json TEXT NOT NULL CHECK (length(feedback_json) > 0),"
        "revision INTEGER NOT NULL CHECK (revision >= 1),"
        "created_at INTEGER NOT NULL,"
        "updated_at INTEGER NOT NULL)"
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT m.ticket_id, t.stage, m.sender_kind, m.sender_id, m.message, "
            "m.created_at, m.updated_at "
            "FROM ticket_rejection_messages m JOIN tickets t ON t.id=m.ticket_id "
            "WHERE m.sequence=2 AND m.state IN ('pending','delivering') "
            "AND NOT EXISTS ("
            "SELECT 1 FROM conversation_events e "
            "WHERE e.conversation_id=t.conversation_id "
            "AND e.kind IN ('prompt','prompt_delivery_uncertain') "
            "AND json_extract(e.payload,'$.sender_message_id') = "
            "'ticket-rejection:' || m.ticket_id || ':' || m.rejection_generation || "
            "':2:' || m.delivery_attempt) "
            "ORDER BY m.ticket_id, m.rejection_generation, m.created_at, m.id"
        )
    ).mappings()
    feedback_by_ticket: dict[str, _FeedbackBatch] = {}
    for row in rows:
        ticket_id = str(row["ticket_id"])
        batch = feedback_by_ticket.setdefault(
            ticket_id,
            {
                "stage": str(row["stage"]),
                "items": [],
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            },
        )
        batch["items"].append(
            {
                "sender_kind": str(row["sender_kind"]),
                "sender_id": str(row["sender_id"]),
                "message": str(row["message"]),
            }
        )
        batch["created_at"] = min(int(batch["created_at"]), int(row["created_at"]))
        batch["updated_at"] = max(int(batch["updated_at"]), int(row["updated_at"]))
    for ticket_id, batch in feedback_by_ticket.items():
        bind.execute(
            sa.text(
                "INSERT INTO ticket_revision_feedback "
                "(ticket_id,stage,feedback_json,revision,created_at,updated_at) "
                "VALUES (:ticket_id,:stage,:feedback_json,1,:created_at,:updated_at)"
            ),
            {
                "ticket_id": ticket_id,
                "stage": batch["stage"],
                "feedback_json": json.dumps(batch["items"], separators=(",", ":")),
                "created_at": batch["created_at"],
                "updated_at": batch["updated_at"],
            },
        )
    op.execute("DELETE FROM conversation_events WHERE kind='proposal_delivery_failed'")
    op.drop_table("proposal_delivery_failures")
    op.drop_table("ticket_rejection_messages")
    op.drop_table("proposal_holder_wakes")


def downgrade() -> None:
    raise NotImplementedError("deleted proposal delivery state cannot be reconstructed")
