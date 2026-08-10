"""Keep every conversation that a Ticket has had.

The active pointer remains on ``tickets``.  This relation records durable ownership,
including conversations recovered from the exact opener that Panels sends to workers.

Revision ID: ticket_conversation_history
Revises: weekly_sprint_checkpoint_schedule
"""

from __future__ import annotations

import json
import re
from typing import Any

from alembic import op
from sqlalchemy.engine import Connection

revision = "ticket_conversation_history"
down_revision = "weekly_sprint_checkpoint_schedule"
branch_labels = None
depends_on = None

CREATE_TICKET_CONVERSATIONS = """
CREATE TABLE ticket_conversations (
  conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id),
  ticket_id       TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE
)
"""

CREATE_TICKET_LOOKUP_INDEX = """
CREATE INDEX idx_ticket_conversations_ticket_id ON ticket_conversations(ticket_id)
"""

_TICKET_ID = r"(?P<ticket_id>t_[a-z0-9]+)"
_HEADER = rf"Work ticket {_TICKET_ID} — [^\n]+\. It is at Stage '[^'\n]+'; "
_ORDINARY = (
    r"take the next step and propose the '[^'\n]+' field for approval\. "
    r"Stage owner: (?:worker|user|paired)\."
)
_PAIRED = (
    r"open the paired discussion for the '[^'\n]+' field\. "
    r"Ask bounded questions or resume the Stage conversation, and do not file a "
    r"proposal until the discussion has enough shared understanding\. Stage owner: paired\."
)
_PENDING_CONTEXT = r"(?:\n\n\[Pending worker context\]\n[\s\S]*\n\[/Pending worker context\])?"
_STANDARD_WORKER_OPENER = re.compile(
    rf"^{_HEADER}(?:{_ORDINARY}|{_PAIRED}){_PENDING_CONTEXT}$"
)


def ticket_id_from_standard_worker_opener(payload: str) -> str | None:
    """Return the Ticket id only when a prompt payload is a standard worker opener."""
    try:
        stored: Any = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(stored, dict) or set(stored).difference(
        {"text", "sender_label", "mode", "sender_message_id", "sent_at_unix_milliseconds"}
    ):
        return None
    text = stored.get("text")
    if (
        not isinstance(text, str)
        or stored.get("sender_label") != "loop"
        or stored.get("mode") != "run_when_free"
    ):
        return None
    matched = _STANDARD_WORKER_OPENER.fullmatch(text)
    return None if matched is None else str(matched.group("ticket_id"))


def _backfill_active_pointers(connection: Connection) -> None:
    rows = connection.exec_driver_sql(
        "SELECT tickets.id, tickets.conversation_id "
        "FROM tickets JOIN conversations ON conversations.conversation_id = tickets.conversation_id "
        "WHERE tickets.conversation_id IS NOT NULL AND tickets.conversation_id IN ("
        "SELECT conversation_id FROM tickets WHERE conversation_id IS NOT NULL "
        "GROUP BY conversation_id HAVING count(*) = 1) ORDER BY tickets.id"
    ).fetchall()
    for ticket_id, conversation_id in rows:
        connection.exec_driver_sql(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket_id),
        )


def _recover_standard_worker_openers(connection: Connection) -> None:
    rows = connection.exec_driver_sql(
        "SELECT conversations.conversation_id, first_prompt.payload "
        "FROM conversations JOIN conversation_events AS first_prompt "
        "ON first_prompt.conversation_id = conversations.conversation_id "
        "AND first_prompt.sequence = ("
        "SELECT MIN(candidate.sequence) FROM conversation_events AS candidate "
        "WHERE candidate.conversation_id = conversations.conversation_id "
        "AND candidate.kind = 'prompt') "
        "LEFT JOIN ticket_conversations "
        "ON ticket_conversations.conversation_id = conversations.conversation_id "
        "WHERE first_prompt.kind = 'prompt' "
        "AND ticket_conversations.conversation_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM tickets "
        "WHERE tickets.conversation_id = conversations.conversation_id) "
        "ORDER BY conversations.created_at, conversations.conversation_id"
    ).fetchall()
    for conversation_id, payload in rows:
        ticket_id = ticket_id_from_standard_worker_opener(payload)
        if ticket_id is None:
            continue
        ticket_exists = connection.exec_driver_sql(
            "SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)
        ).first()
        if ticket_exists is None:
            continue
        connection.exec_driver_sql(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket_id),
        )


def upgrade() -> None:
    connection = op.get_bind()
    op.execute(CREATE_TICKET_CONVERSATIONS)
    op.execute(CREATE_TICKET_LOOKUP_INDEX)
    _backfill_active_pointers(connection)
    _recover_standard_worker_openers(connection)


def downgrade() -> None:
    raise NotImplementedError("dropping Ticket conversation history loses recovered ownership")
