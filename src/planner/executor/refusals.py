"""Show a queued Panels tool's later refusal in its source transcript."""

from __future__ import annotations

import json
import sqlite3

from planner.conversation.events import (
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    ToolCallStatus,
)
from planner.conversation.storage import ConversationStore
from planner.executor.contracts import JsonObject, QueuedEnvelope


def record_refused_write(
    connection: sqlite3.Connection, db_path: str, envelope: QueuedEnvelope, result: JsonObject
) -> None:
    child = envelope.payload.get("child")
    if not isinstance(child, dict):
        return
    # A malformed or unassigned frame cannot write into an arbitrary transcript.
    assigned = connection.execute(
        "SELECT 1 FROM executor_child_assignments a JOIN conversations c "
        "ON c.conversation_id=a.conversation_id WHERE a.executor_incarnation=? AND a.child_id=? "
        "AND a.conversation_id=?",
        (child.get("executor_incarnation"), child.get("child_id"), envelope.conversation_id),
    ).fetchone()
    if assigned is None:
        return
    operation = str(envelope.payload.get("operation", "Panels write"))
    call_id = "outbox_refusal_" + envelope.submission_id
    ConversationStore(db_path).append_in_transaction(
        connection,
        envelope.conversation_id,
        (
            ToolCallStartedEventPayload(call_id, f"Queued {operation} refused", "other", None),
            ToolCallFinishedEventPayload(call_id, ToolCallStatus.failed, json.dumps(result)),
        ),
    )
