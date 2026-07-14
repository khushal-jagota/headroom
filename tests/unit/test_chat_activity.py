from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

from planner.chat import data as chat_data
from planner.chat import service as chat_service
from planner.chat.contracts import (
    ChatActivityObservation,
    ChatTurnRequest,
    HumanChatCompletion,
)
from planner.chat.data import MAX_ACTIVE_TURN_ACTIVITY_ENTRIES
from planner.chat.logic.activity import normalize_gateway_activity
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.db import connect, create_schema

_CURRENT_CHAT_TURNS_DDL = """
CREATE TABLE chat_turns (
  id             TEXT PRIMARY KEY,
  entity_id      TEXT NOT NULL,
  origin         TEXT NOT NULL CHECK (origin IN ('human','worker','system')),
  mode           TEXT NOT NULL CHECK (mode IN ('message','command','worker_step')),
  status         TEXT NOT NULL CHECK (status IN ('running','complete','errored','interrupted')),
  phase          TEXT NOT NULL
                 CHECK (phase IN ('queued','thinking','doing','responding','settled')),
  activity_label TEXT,
  output_role    TEXT NOT NULL CHECK (output_role IN ('assistant','system')),
  output_text    TEXT NOT NULL DEFAULT '',
  session_key    TEXT,
  error          TEXT,
  started_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  completed_at   INTEGER
);
"""


def _turn(conn: sqlite3.Connection, entity_id: str = "t_activity"):
    return chat_data.start_turn(
        conn,
        entity_id,
        origin="human",
        mode="message",
        visible_role="human",
        visible_text="show activity",
        output_role="assistant",
        phase="thinking",
        activity_label="Thinking",
        now=1,
    )


def test_create_schema_migrates_current_chat_turn_schema_with_activity_fk(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "current-chat.db"))
    conn.executescript(_CURRENT_CHAT_TURNS_DDL)

    create_schema(conn)

    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(chat_turn_activity_entries)")
    }
    assert columns == {
        "id",
        "turn_id",
        "action_identity",
        "category",
        "label",
        "lifecycle_state",
        "started_at",
        "updated_at",
        "completed_at",
    }
    foreign_keys = conn.execute(
        "PRAGMA foreign_key_list(chat_turn_activity_entries)"
    ).fetchall()
    assert [
        (row["table"], row["from"], row["to"], row["on_delete"])
        for row in foreign_keys
    ] == [("chat_turns", "turn_id", "id", "CASCADE")]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_active_turn_reads_activity_entries_in_stable_insert_order(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "ordered.db"))
    create_schema(conn)
    turn = _turn(conn)

    chat_data.record_turn_activity(
        conn,
        turn.id,
        entity_id=turn.entity_id,
        observation=ChatActivityObservation(
            category="thinking",
            label="Thinking",
            lifecycle_state="running",
            action_identity="thinking",
        ),
        now=2,
    )
    chat_data.record_turn_activity(
        conn,
        turn.id,
        entity_id=turn.entity_id,
        observation=ChatActivityObservation(
            category="tool",
            label="Using web_search",
            lifecycle_state="running",
            action_identity="tool:call-2",
        ),
        now=2,
    )

    state = chat_data.read_state(conn, turn.entity_id, session_key=None)

    assert state.active_turn is not None
    assert [entry.label for entry in state.active_turn.activity_entries] == [
        "Thinking",
        "Using web_search",
    ]
    first, second = state.active_turn.activity_entries
    assert first.id < second.id
    assert first.started_at == first.updated_at == 2
    assert first.completed_at is None
    assert second.action_identity == "tool:call-2"
    conn.close()


def test_activity_identity_updates_one_row_and_idless_repeats_do_not_spam(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "dedupe.db"))
    create_schema(conn)
    turn = _turn(conn)
    started = ChatActivityObservation(
        category="tool",
        label="Using read_file",
        lifecycle_state="running",
        action_identity="tool:call-1",
    )
    chat_data.record_turn_activity(
        conn, turn.id, entity_id=turn.entity_id, observation=started, now=2
    )
    original_id = chat_data.read_active_turn(conn, turn.entity_id).activity_entries[0].id  # type: ignore[union-attr]
    chat_data.record_turn_activity(
        conn,
        turn.id,
        entity_id=turn.entity_id,
        observation=ChatActivityObservation(
            category="tool",
            label="Used read_file",
            lifecycle_state="complete",
            action_identity="tool:call-1",
        ),
        now=4,
    )
    repeated_delta = ChatActivityObservation(
        category="thinking",
        label="Thinking",
        lifecycle_state="running",
    )
    chat_data.record_turn_activity(
        conn, turn.id, entity_id=turn.entity_id, observation=repeated_delta, now=5
    )
    chat_data.record_turn_activity(
        conn, turn.id, entity_id=turn.entity_id, observation=repeated_delta, now=6
    )

    active = chat_data.read_active_turn(conn, turn.entity_id)
    assert active is not None
    assert len(active.activity_entries) == 2
    completed, thinking = active.activity_entries
    assert completed.id == original_id
    assert completed.label == "Used read_file"
    assert completed.lifecycle_state == "complete"
    assert completed.started_at == 2
    assert completed.updated_at == completed.completed_at == 4
    assert thinking.label == "Thinking"
    conn.close()


def test_activity_keeps_exactly_the_newest_named_limit(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "bounded.db"))
    create_schema(conn)
    turn = _turn(conn)

    for index in range(MAX_ACTIVE_TURN_ACTIVITY_ENTRIES + 1):
        chat_data.record_turn_activity(
            conn,
            turn.id,
            entity_id=turn.entity_id,
            observation=ChatActivityObservation(
                category="tool",
                label=f"Using tool {index}",
                lifecycle_state="running",
                action_identity=f"tool:call-{index}",
            ),
            now=index + 2,
        )

    active = chat_data.read_active_turn(conn, turn.entity_id)
    assert active is not None
    assert len(active.activity_entries) == MAX_ACTIVE_TURN_ACTIVITY_ENTRIES == 100
    assert [entry.label for entry in active.activity_entries] == [
        f"Using tool {index}" for index in range(1, 101)
    ]
    conn.close()


def test_finish_fail_and_interrupted_settlement_delete_activity(tmp_path: Path) -> None:
    for settlement in ("complete", "errored", "interrupted"):
        conn = connect(str(tmp_path / f"settle-{settlement}.db"))
        create_schema(conn)
        turn = _turn(conn, f"t_{settlement}")
        chat_data.record_turn_activity(
            conn,
            turn.id,
            entity_id=turn.entity_id,
            observation=ChatActivityObservation(
                category="thinking",
                label="Thinking",
                lifecycle_state="running",
                action_identity="thinking",
            ),
            now=2,
        )
        if settlement == "errored":
            chat_data.fail_turn(
                conn, turn.id, entity_id=turn.entity_id, error="failed", now=3
            )
        else:
            chat_data.finish_turn(
                conn,
                turn.id,
                entity_id=turn.entity_id,
                reply_text="done",
                output_role="assistant",
                status=settlement,
                now=3,
            )

        assert conn.execute(
            "SELECT COUNT(*) FROM chat_turn_activity_entries WHERE turn_id = ?",
            (turn.id,),
        ).fetchone()[0] == 0
        conn.close()


def test_deleting_chat_turn_cascades_activity_entries(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "cascade.db"))
    create_schema(conn)
    turn = _turn(conn)
    chat_data.record_turn_activity(
        conn,
        turn.id,
        entity_id=turn.entity_id,
        observation=ChatActivityObservation(
            category="thinking",
            label="Thinking",
            lifecycle_state="running",
            action_identity="thinking",
        ),
        now=2,
    )
    conn.execute("DELETE FROM chat_messages WHERE turn_id = ?", (turn.id,))
    conn.execute("DELETE FROM chat_turns WHERE id = ?", (turn.id,))

    assert conn.execute(
        "SELECT COUNT(*) FROM chat_turn_activity_entries WHERE turn_id = ?", (turn.id,)
    ).fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_normalizer_maps_supported_hermes_events_without_raw_payload_fields() -> None:
    thinking = normalize_gateway_activity(
        "reasoning.delta", {"text": "private chain of thought must not persist"}
    )
    command_start = normalize_gateway_activity(
        "tool.start",
        {
            "tool_id": "call-terminal-1",
            "name": "terminal",
            "context": "rm -rf / raw command",
            "args_text": "api_key=secret",
        },
    )
    command_end = normalize_gateway_activity(
        "tool.complete",
        {
            "tool_id": "call-terminal-1",
            "name": "terminal",
            "args": {"command": "rm -rf /"},
            "result": "raw command output",
            "result_text": "secret output",
        },
    )
    tool_start = normalize_gateway_activity(
        "tool.start", {"tool_id": "call-web-1", "name": "web_search", "context": "secret"}
    )
    slash_command = normalize_gateway_activity(
        "command.start",
        {"id": "command-1", "name": "/status", "args_text": "secret argument"},
    )
    malformed_tool = normalize_gateway_activity(
        "tool.start",
        {
            "tool_id": {"args": "secret identity"},
            "name": {"result": "secret label"},
            "tool": {"name": {"output": "secret nested label"}},
        },
    )

    assert thinking == ChatActivityObservation(
        category="thinking",
        label="Thinking",
        lifecycle_state="running",
        action_identity="thinking",
    )
    assert command_start == ChatActivityObservation(
        category="command",
        label="Running terminal",
        lifecycle_state="running",
        action_identity="tool:call-terminal-1",
    )
    assert command_end == ChatActivityObservation(
        category="command",
        label="Ran terminal",
        lifecycle_state="complete",
        action_identity="tool:call-terminal-1",
    )
    assert tool_start == ChatActivityObservation(
        category="tool",
        label="Using web_search",
        lifecycle_state="running",
        action_identity="tool:call-web-1",
    )
    assert slash_command == ChatActivityObservation(
        category="command",
        label="Running /status",
        lifecycle_state="running",
        action_identity="command:command-1",
    )
    assert malformed_tool == ChatActivityObservation(
        category="tool",
        label="Using tool",
        lifecycle_state="running",
        action_identity=None,
    )
    assert normalize_gateway_activity(
        "message.delta", {"text": "visible answer, not activity"}
    ) is None
    assert set(asdict(command_end)) == {
        "category",
        "label",
        "lifecycle_state",
        "action_identity",
    }
    serialized = repr(
        (thinking, command_start, command_end, tool_start, slash_command, malformed_tool)
    )
    for unsafe in (
        "chain of thought",
        "rm -rf",
        "api_key",
        "raw command output",
        "secret",
        "secret argument",
        "secret identity",
        "secret label",
        "secret nested label",
    ):
        assert unsafe not in serialized


def test_worker_gateway_producer_uses_normalizer_and_updates_one_action(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "worker-producer.db"))
    create_schema(conn)
    turn = chat_service.start_worker_turn(conn, "t_worker", visible_text="work", now=1)

    chat_service.observe_worker_gateway_event(
        conn,
        turn.entity_id,
        turn.id,
        {
            "type": "tool.start",
            "payload": {
                "tool_id": "call-1",
                "name": "terminal",
                "context": "private command",
            },
        },
        2,
    )
    chat_service.observe_worker_gateway_event(
        conn,
        turn.entity_id,
        turn.id,
        {
            "type": "tool.complete",
            "payload": {
                "tool_id": "call-1",
                "name": "terminal",
                "result": "private output",
            },
        },
        3,
    )

    active = chat_data.read_active_turn(conn, turn.entity_id)
    assert active is not None
    assert active.activity_label == "Ran terminal"
    observed_entries = [
        (entry.category, entry.label, entry.lifecycle_state)
        for entry in active.activity_entries
    ]
    assert observed_entries == [
        ("command", "Ran terminal", "complete")
    ]
    conn.close()


def test_human_turn_persists_typed_activity_observation(tmp_path: Path) -> None:
    db_path = tmp_path / "human-producer.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    observed_entries: list[tuple[str, str, str]] = []

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    class HumanActivityGateway:
        def run_human_turn(  # noqa: ANN201
            self, session_key, entity_id, text, mode, bind_session_key, image_paths=()
        ):  # noqa: ANN001
            bind_session_key("human-session")
            observation = normalize_gateway_activity(
                "tool.start",
                {
                    "tool_id": "call-human-1",
                    "name": "read_file",
                    "args_text": "never persist me",
                },
            )
            assert observation is not None
            yield observation
            inspect = conn_factory()
            try:
                active = chat_data.read_active_turn(inspect, entity_id)
                assert active is not None
                observed_entries.extend(
                    (entry.category, entry.label, entry.lifecycle_state)
                    for entry in active.activity_entries
                )
            finally:
                inspect.close()
            yield HumanChatCompletion("done", "assistant")

    lifecycle = chat_service.ChatTurnLifecycle(
        conn_factory,
        lambda: HumanActivityGateway(),  # type: ignore[arg-type,return-value]
        lambda: 2,
        db_path,
    )
    turn = lifecycle.start_human_turn(
        CHIEF_OF_STAFF_ENTITY_ID, ChatTurnRequest(text="hello")
    )

    for _ in range(40):
        settled = conn_factory()
        try:
            status = settled.execute(
                "SELECT status FROM chat_turns WHERE id = ?", (turn.id,)
            ).fetchone()[0]
        finally:
            settled.close()
        if status == "complete":
            break
        threading.Event().wait(0.05)
    else:
        raise AssertionError("human Chat turn did not settle")

    assert observed_entries == [("tool", "Using read_file", "running")]
    final_conn = conn_factory()
    try:
        assert final_conn.execute(
            "SELECT COUNT(*) FROM chat_turn_activity_entries WHERE turn_id = ?", (turn.id,)
        ).fetchone()[0] == 0
    finally:
        final_conn.close()
