"""Protect the old reader's semantics at the new canonical projection boundary.

Existing browser fixtures check individual UI rules. This shared oracle checks the
complete old TypeScript pipeline against Python source references and turn aggregates.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.events import (
    ConversationEventKind,
    conversation_event_payload_from_canonical_json,
)
from planner.conversation.storage import StoredConversationEvent
from planner.conversation.views.schema import SCHEMA_STATEMENTS
from planner.conversation.views.storage import (
    encode_wire,
    read_group,
    read_page,
    read_row,
    read_turn,
)
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


def legacy_row(row: dict[str, Any]) -> dict[str, Any]:
    body = row["body"]["value"]
    names = {
        "sender_label": "senderLabel",
        "sent_at_unix_milliseconds": "sentAtUnixMilliseconds",
        "to_owner": "toOwner",
        "prompt_sender": "promptSender",
        "tool_call_id": "toolCallId",
        "tool_kind": "toolKind",
        "started_detail": "startedDetail",
        "capped_detail_sequence": "cappedDetailSequence",
        "ask_id": "askId",
        "dead_reason": "deadReason",
        "answered_option_label": "answeredOptionLabel",
        "request_id": "requestId",
        "failure_detail": "failureDetail",
        "reasoning_effort": "reasoningEffort",
        "input_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "cached_input_tokens": "cachedInputTokens",
        "cost_usd": "costUsd",
        "error_summary": "errorSummary",
        "automatic_compaction_result": "automaticCompactionResult",
    }
    result = {
        "key": row["key"],
        "sequence": row["source_sequence"],
        "createdAt": row["created_at"],
        **{
            names.get(key, key): value
            for key, value in body.items()
        },
    }
    if result["kind"] == "prompt_refused":
        result["sentence"] = result.pop("refusal_detail")
    if result["kind"] == "tool_call":
        result["progress"] = None
    if result["kind"] == "user_input" and result["answers"] is not None:
        result["answers"] = {
            answer["question_id"]: {"answers": answer["answers"]} for answer in result["answers"]
        }
    return result


def expanded_items(conn: sqlite3.Connection, lens: str, stopped: bool) -> list[dict[str, Any]]:
    focus = lens == "focus"
    ordered: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for source in conn.execute(
        "SELECT * FROM conversation_view_rows WHERE removed=0 ORDER BY sequence"
    ):
        if focus and not source["focus_visible"]:
            continue
        if source["row_kind"] == "tool_call":
            continue
        view, _ = read_row(conn, "fixture", source["sequence"], stopped=stopped)
        row = legacy_row(json.loads(encode_wire(view)))
        behind = None
        if (
            source["row_kind"] == "agent_message"
            and not row.get("toOwner")
            and source["turn_key"] is not None
        ):
            turn = conn.execute(
                "SELECT * FROM conversation_view_turns WHERE turn_key=?", (source["turn_key"],)
            ).fetchone()
            last = (
                turn["latest_focus_message_sequence"]
                if focus
                else turn["latest_full_message_sequence"]
            )
            if (
                (not focus or turn["focus_head"])
                and (stopped or turn["ending_sequence"] is not None)
                and source["sequence"] != last
            ):
                behind = source["turn_key"]
        ordered.append(
            (
                (source["sequence"], 0),
                {"kind": "row", "key": row["key"], "row": row, "behindTheFoldOf": behind},
            )
        )
    for source in conn.execute("SELECT * FROM conversation_view_turns ORDER BY source_sequence"):
        if focus and not source["focus_head"]:
            continue
        turn = read_turn(
            conn, "fixture", source["turn_key"], "focus" if focus else "full", stopped=stopped
        )
        item = {
            "kind": "turn",
            "key": turn["key"],
            "turnKey": turn["key"],
            "settled": turn["settled"],
            "stopped": turn["stopped"],
            "startedAtUnixMilliseconds": turn["started_at_unix_milliseconds"],
            "ending": turn["ending"],
            "isLatest": turn["is_latest"],
            "durationSeconds": turn["duration_seconds"],
            "toolCallCount": turn["tool_call_count"],
            "foldedMessageCount": turn["folded_message_count"],
        }
        ordered.append(((source["source_sequence"], 1), item))
    if not focus:
        for source in conn.execute(
            "SELECT * FROM conversation_view_groups ORDER BY source_sequence"
        ):
            group = read_group(conn, "fixture", source["group_key"], stopped=stopped)
            entries = []
            for child in conn.execute(
                "SELECT sequence FROM conversation_view_rows "
                "WHERE work_group_key=? AND removed=0 ORDER BY sequence",
                (group["key"],),
            ):
                view, _ = read_row(conn, "fixture", child[0], stopped=stopped)
                entries.append(legacy_row(json.loads(encode_wire(view))))
            ordered.append(
                (
                    (source["source_sequence"], 0),
                    {
                        "kind": "work_group",
                        "key": group["key"],
                        "turnKey": group["turn_key"] or "turn:none",
                        "entries": entries,
                        "settled": group["settled"],
                    },
                )
            )
    if stopped:
        last = conn.execute(
            "SELECT MAX(sequence),MAX(created_at) FROM conversation_events"
        ).fetchone()
        row = {
            "key": "turn-stopped",
            "kind": "turn_stopped",
            "sequence": last[0] + 1,
            "createdAt": last[1],
        }
        ordered.append(
            (
                (row["sequence"], 0),
                {"kind": "row", "key": row["key"], "row": row, "behindTheFoldOf": None},
            )
        )
    return [item for _, item in sorted(ordered, key=lambda pair: pair[0])]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["name"])
@pytest.mark.parametrize("lens", ["focus", "full"])
def test_projected_semantics_preserve_full_latest_message_boundary(
    fixture: dict[str, Any], lens: str
) -> None:
    # Legacy keeps the final ordinary message after excluding owner messages. The
    # projected reader deliberately keeps its existing latest-full-message boundary.
    # Full automatically expands both presentations, so every message remains visible.
    expected = copy.deepcopy(fixture["expected"][lens]["items"])
    with database(fixture["events"]) as conn:
        if lens == "full":
            for turn in (item for item in expected if item["kind"] == "turn"):
                messages = {
                    f"e{source['sequence']}": source
                    for source in conn.execute(
                        "SELECT * FROM conversation_view_rows WHERE turn_key=? "
                        "AND row_kind='agent_message' ORDER BY sequence",
                        (turn["key"],),
                    )
                }
                last = next(reversed(messages), None)
                folded = 0
                for item in expected:
                    if item["kind"] != "row" or item["key"] not in messages:
                        continue
                    behind = (
                        turn["key"]
                        if turn["settled"]
                        and not item["row"].get("toOwner")
                        and item["key"] != last
                        else None
                    )
                    item["behindTheFoldOf"] = behind
                    folded += int(behind is not None)
                turn["foldedMessageCount"] = folded
        assert expanded_items(conn, lens, fixture["stopped"]) == expected
        page = read_page(
            conn, "fixture", "focus" if lens == "focus" else "full", stopped=fixture["stopped"]
        )
        assert len(page["items"]) <= 100
        assert page["body_bytes"] <= 128 * 1024
