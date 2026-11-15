"""Stored memory-notice turns stop authorizing one more automatic compaction on upgrade.

Risk: a Worker whose only prompts since its last automatic attempt are memory-loss notices
was recorded by the previous release as having new activity, so the first sweep after
rollout compacts it again and the idle loop continues. Or the upgrade over-matches and
takes eligibility from a conversation that did real work, including a person's own
``/compact``. Gap: no other test seeds history at the previous head. Layer: the real
migrations on a SQLite file, upgraded by ``create_schema`` like application startup, then
the persisted candidate query the sweep uses.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from tests.support.migration_head import shipped_migration_head

from planner.conversation.storage import ConversationStore
from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)

PREVIOUS_HEAD = "retire_executor_websocket_tables"
OWNER = {"kind": "owner", "id": "owner"}
ACTIVITY_AT = 10_000


def _prompt(text: str, sender_label: str, **identity: object) -> dict[str, object]:
    return {"text": text, "sender_label": sender_label, "mode": "queue", **identity}


# Each history is one work turn, the automatic /compact that set the watermark at 2, and
# then the prompt that ended the recorded activity at 9.
LATEST_PROMPT = {
    "notice_only": _prompt(
        "Your context was compacted", "Panels", sender_message_id="worker_step_memory_notice_a"
    ),
    "owner_message": _prompt("more work", "owner", sender=OWNER),
    "owner_compact": _prompt("/compact", "owner", sender=OWNER),
}


def _seed(conn: sqlite3.Connection, conversation_id: str, latest_prompt: dict[str, object]) -> None:
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, access, "
        "created_at, latest_sequence, latest_agent_activity_at, latest_agent_activity_sequence, "
        "automatically_compacted_through_sequence, "
        "automatic_compaction_attempted_through_sequence) "
        "VALUES (?, 'claude', '/tmp', 'full', 0, 9, ?, 9, 2, 2)",
        (conversation_id, ACTIVITY_AT),
    )
    events: tuple[tuple[int, str, dict[str, object]], ...] = (
        (1, "prompt", _prompt("real work", "owner", sender=OWNER)),
        (2, "turn_ended", {"ending": "completed", "error_summary": None}),
        (3, "prompt", _prompt("/compact", "Panels", system_generated=True)),
        (4, "context_compacted", {}),
        (5, "turn_ended", {"ending": "completed", "error_summary": None}),
        (6, "prompt", latest_prompt),
        (9, "turn_ended", {"ending": "completed", "error_summary": None}),
    )
    for sequence, kind, payload in events:
        conn.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
            "created_at) VALUES (?, ?, ?, ?, 0)",
            (conversation_id, sequence, kind, json.dumps(payload)),
        )


def test_only_notice_only_activity_loses_its_compaction_eligibility(tmp_path: Path) -> None:
    path = str(tmp_path / "previous.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, PREVIOUS_HEAD)
    with closing(connect(path)) as conn:
        with conn:
            for conversation_id, latest_prompt in LATEST_PROMPT.items():
                _seed(conn, conversation_id, latest_prompt)

        create_schema(conn)

        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            shipped_migration_head()
        )
        watermarks = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT conversation_id, automatic_compaction_attempted_through_sequence, "
                "automatically_compacted_through_sequence FROM conversations"
            )
        }
    assert watermarks == {"notice_only": (9, 2), "owner_message": (2, 2), "owner_compact": (2, 2)}
    due = asyncio.run(
        ConversationStore(path).conversations_due_for_automatic_compaction(
            due_at_or_before=ACTIVITY_AT, activity_after=ACTIVITY_AT - 600
        )
    )
    assert due == ("owner_compact", "owner_message")
