"""Small canonical conversation database shared by current view tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from planner.conversation.events import (
    ConversationEventKind,
    conversation_event_payload_from_canonical_json,
)
from planner.conversation.storage import StoredConversationEvent
from planner.conversation.views.schema import SCHEMA_STATEMENTS
from planner.conversation.views.writer import append_projection
from planner.maintenance.diagnostic_history import install_expiry_metadata

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/conversation_views.json").read_text())


def database(events: list[dict[str, Any]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY,backend_key TEXT)")
    conn.execute("INSERT INTO conversations VALUES ('fixture','hermes')")
    conn.execute(
        "CREATE TABLE conversation_events(conversation_id TEXT,sequence INTEGER,kind TEXT,"
        "payload TEXT,created_at INTEGER,PRIMARY KEY(conversation_id,sequence))"
    )
    conn.execute(
        "CREATE TABLE conversation_background_tasks(conversation_id TEXT,task_id TEXT,"
        "task_description TEXT,PRIMARY KEY(conversation_id,task_id))"
    )
    install_expiry_metadata(conn)
    for sql in SCHEMA_STATEMENTS:
        conn.execute(sql)
    for row in events:
        encoded = json.dumps(row["payload"])
        conn.execute(
            "INSERT INTO conversation_events VALUES (?,?,?,?,?)",
            (
                "fixture",
                row["sequence"],
                row["kind"],
                encoded,
                row["created_at"],
            ),
        )
        kind = ConversationEventKind(row["kind"])
        append_projection(
            conn,
            [
                StoredConversationEvent(
                    "fixture",
                    row["sequence"],
                    kind,
                    conversation_event_payload_from_canonical_json(kind, encoded),
                    row["created_at"],
                )
            ],
        )
    return conn
