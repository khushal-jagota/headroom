"""Retiring the reply requirement leaves a stored marker's conversation as if never projected.

Risk: deleting the projected explicit_reply_missing rows strands the rest-line pointers
on missing rows, or moves them onto rows whose cached rest summary was cleared, so the
status line shows nothing. No other test seeds a database at the previous head with the
old projection. The real chain at goal_holders is the cheapest faithful schema; the
events go through the real projection writer, which also proves a stored marker loads
and projects to nothing.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from tests.unit.test_memory_notice_migration import _database_at_revision
from tests.unit.test_worker_memory import _make_conversation

from planner.conversation.events import (
    ConversationEventKind,
    conversation_event_payload_from_canonical_json,
)
from planner.conversation.storage import StoredConversationEvent
from planner.conversation.views.live_output import ConversationViewLiveOutput
from planner.conversation.views.status import read_status
from planner.conversation.views.writer import append_projection
from planner.core.db import connect, create_schema

OWNER = {"kind": "owner", "id": "owner"}
EVENTS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "prompt",
        {
            "sender_label": "owner",
            "content": [{"piece": "text", "text": "Please fix the login bug"}],
            "mode": "queue",
            "sender": OWNER,
        },
    ),
    ("agent_message", {"content": [{"piece": "text", "text": "Checking the session logs"}]}),
    ("explicit_reply_missing", {"prompt_sender": OWNER}),
    ("turn_ended", {"ending": "completed", "error_summary": None}),
)


def _projection(conn: sqlite3.Connection) -> tuple[list[tuple[Any, ...]], tuple[Any, ...]]:
    rows = [
        tuple(row) for row in conn.execute("SELECT * FROM conversation_view_rows ORDER BY sequence")
    ]
    state = conn.execute(
        "SELECT latest_full_happened_sequence,latest_focus_happened_sequence "
        "FROM conversation_view_state WHERE conversation_id='retired'"
    ).fetchone()
    return rows, tuple(state)


def test_upgrade_deletes_marker_rows_and_moves_the_rest_line_to_what_remains(
    tmp_path: Path,
) -> None:
    path = _database_at_revision(tmp_path, "goal_holders")
    with closing(connect(path)) as conn:
        _make_conversation(conn, "retired", compacted=0)
        for sequence, (kind, payload) in enumerate(EVENTS, start=1):
            encoded = json.dumps(payload)
            conn.execute(
                "INSERT INTO conversation_events VALUES ('retired',?,?,?,?)",
                (sequence, kind, encoded, 100 + sequence),
            )
            event_kind = ConversationEventKind(kind)
            append_projection(
                conn,
                [
                    StoredConversationEvent(
                        "retired",
                        sequence,
                        event_kind,
                        conversation_event_payload_from_canonical_json(event_kind, encoded),
                        100 + sequence,
                    )
                ],
            )
        never_projected = _projection(conn)
        # What the retired projection left: a focus-visible marker row in the turn that
        # both pointers name, with the rows they used to name stripped of their summary.
        conn.execute(
            "INSERT INTO conversation_view_rows(conversation_id,sequence,row_kind,created_at,"
            "changed_sequence,turn_key,focus_visible,rest_summary) "
            "VALUES ('retired',3,'explicit_reply_missing',103,3,'turn:e1',1,?)",
            (json.dumps({"kind": "explicit_reply_missing", "prompt_sender": OWNER}),),
        )
        conn.execute(
            "UPDATE conversation_view_state SET latest_full_happened_sequence=3,"
            "latest_focus_happened_sequence=3"
        )
        conn.execute("UPDATE conversation_view_rows SET rest_summary=NULL WHERE sequence<3")
        conn.commit()

        create_schema(conn)

        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='conversation_reply_credits'"
            ).fetchone()
            is None
        )
        assert _projection(conn) == never_projected
        assert [
            (row["sequence"], row["row_kind"])
            for row in conn.execute(
                "SELECT sequence,row_kind FROM conversation_view_rows ORDER BY sequence"
            )
        ] == [(1, "prompt"), (2, "agent_message"), (4, "turn_ended")]
        assert never_projected[1] == (2, 1)
        live = ConversationViewLiveOutput()
        assert read_status(conn, "retired", "full", live, running=False)["rest"][
            "latest_happened"
        ] == {"kind": "agent_message", "sender_label": None, "excerpt": "Checking the session logs"}
        assert read_status(conn, "retired", "focus", live, running=False)["rest"][
            "latest_happened"
        ] == {"kind": "prompt", "sender_label": "owner", "excerpt": "Please fix the login bug"}
