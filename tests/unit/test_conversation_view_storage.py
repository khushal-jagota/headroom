"""Bounds, source-body integrity, and atomic upgrade checks absent from UI tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.unit.test_conversation_view_projection import FIXTURES, database

from planner.conversation.events import (
    ConversationEventKind,
    conversation_event_payload_from_canonical_json,
)
from planner.conversation.storage import StoredConversationEvent
from planner.conversation.views.api import _changes
from planner.conversation.views.configuration import BODY_CHUNK_BYTES
from planner.conversation.views.contracts import ViewHistoryScope
from planner.conversation.views.live_output import ConversationViewLiveOutput
from planner.conversation.views.status import read_status
from planner.conversation.views.storage import (
    ViewBodyChanged,
    body_content,
    iter_body_content,
    read_body_chunk,
    read_page,
)
from planner.conversation.views.writer import append_projection, backfill_projection


def event(sequence: int, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"sequence": sequence, "created_at": sequence, "kind": kind, "payload": payload}


def prompt(sequence: int = 1) -> dict[str, Any]:
    return event(sequence, "prompt", {"sender_label": "owner", "mode": "queue", "text": "Work"})


def test_closed_turn_does_not_page_its_thousand_hidden_tool_bodies() -> None:
    events = [prompt()]
    events.extend(
        event(
            index,
            "tool_call_started",
            {
                "tool_call_id": f"tool-{index}",
                "title": "Tool",
                "tool_kind": "other",
                "detail": "x" * 1024,
            },
        )
        for index in range(2, 1002)
    )
    events.append(event(1002, "agent_message", {"text": "The answer"}))
    events.append(event(1003, "turn_ended", {"ending": "completed", "error_summary": None}))
    with database(events) as conn:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        page = read_page(conn, "fixture", "full")
        assert [item["key"] for item in page["items"]] == ["e1", "turn:e1", "e1002", "e1003"]
        assert sum("SELECT kind,payload" in sql for sql in statements) == 3
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM conversation_view_rows WHERE rest_summary IS NOT NULL"
            ).fetchone()[0]
            <= 3
        )
        expanded = read_page(conn, "fixture", "full", scope={"kind": "turn", "key": "turn:e1"})
        assert [item["key"] for item in expanded["items"]] == ["work:e2"]
        children = read_page(
            conn, "fixture", "full", scope={"kind": "work_group", "key": "work:e2"}
        )
        assert 0 < len(children["items"]) <= 100
        assert children["body_bytes"] <= 128 * 1024
        assert children["older"] is not None
        older = read_page(
            conn,
            "fixture",
            "full",
            scope={"kind": "work_group", "key": "work:e2"},
            cursor=children["older"],
        )
        assert not {item["key"] for item in children["items"]} & {
            item["key"] for item in older["items"]
        }
        assert 0 < len(older["items"]) <= 100
        assert older["body_bytes"] <= 128 * 1024


def test_oversized_body_chunks_preserve_unicode_and_one_logical_markdown_document() -> None:
    text = "# Title\n\n```text\n" + "界🙂" * 40_000 + "\n```\n\n[reference]: /target\n"
    with database([prompt(), event(2, "agent_message", {"text": text})]) as conn:
        page = read_page(conn, "fixture", "full")
        row = next(item for item in page["items"] if item["key"] == "e2")
        assert row["kind"] == "row" and row["body"]["kind"] == "deferred"
        reference = row["body"]["reference"]
        chunks: list[str] = []
        offset: int | None = 0
        while offset is not None:
            chunk = read_body_chunk(conn, "fixture", reference, offset)
            assert len(chunk["text"].encode("utf-8")) <= BODY_CHUNK_BYTES
            chunks.append(chunk["text"])
            offset = chunk["next_offset"]
        assert json.loads("".join(chunks))["content"][0]["text"] == text
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        streamed = b"".join(iter_body_content(body_content(conn, "fixture", reference)))
        assert json.loads(streamed)["content"][0]["text"] == text
        assert len(streamed) == reference["byte_count"]
        assert sum("SELECT kind,payload" in sql for sql in statements) == 1
        conn.execute("UPDATE conversation_view_rows SET changed_sequence=3 WHERE sequence=2")
        with pytest.raises(ViewBodyChanged):
            read_body_chunk(conn, "fixture", reference, 0)


def test_projection_writes_share_source_rollback_and_upgrade_matches_incremental() -> None:
    with database(FIXTURES[0]["events"]) as conn:
        conn.commit()
        before = list(conn.execute("SELECT * FROM conversation_view_rows ORDER BY sequence"))
        encoded = json.dumps({"text": "Must roll back"})
        with pytest.raises(RuntimeError), conn:
            conn.execute(
                "INSERT INTO conversation_events VALUES ('fixture',100,'agent_message',?,100)",
                (encoded,),
            )
            append_projection(
                conn,
                [
                    StoredConversationEvent(
                        "fixture",
                        100,
                        ConversationEventKind.agent_message,
                        conversation_event_payload_from_canonical_json(
                            ConversationEventKind.agent_message, encoded
                        ),
                        100,
                    )
                ],
            )
            raise RuntimeError("rollback")
        assert (
            list(conn.execute("SELECT * FROM conversation_view_rows ORDER BY sequence")) == before
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM conversation_events WHERE sequence=100").fetchone()[
                0
            ]
            == 0
        )
        for table in ("lookups", "groups", "turns", "rows", "state"):
            conn.execute(f"DELETE FROM conversation_view_{table}")
        original_factory = conn.row_factory
        conn.row_factory = None
        backfill_projection(conn)
        assert conn.row_factory is None
        conn.row_factory = original_factory
        assert (
            list(conn.execute("SELECT * FROM conversation_view_rows ORDER BY sequence")) == before
        )


def test_tail_names_scope_membership_and_removes_children_from_the_timeline() -> None:
    rows = [
        prompt(),
        event(2, "agent_message", {"text": "Earlier"}),
        event(3, "agent_message", {"text": "Answer"}),
        event(4, "turn_ended", {"ending": "completed", "error_summary": None}),
    ]
    with database(rows) as conn:
        conn.execute("ALTER TABLE conversations ADD COLUMN latest_sequence INTEGER DEFAULT 4")
        live = ConversationViewLiveOutput()
        try:
            status = read_status(conn, "fixture", "full", live, running=False)
            timeline: ViewHistoryScope = {"kind": "timeline", "key": None}
            turn: ViewHistoryScope = {"kind": "turn", "key": "turn:e1"}
            changes = _changes(
                conn,
                "fixture",
                "full",
                0,
                [
                    {
                        "scope": timeline,
                        "first": {"sequence": 1, "slot": 1},
                        "last": {"sequence": 4, "slot": 0},
                    },
                    {
                        "scope": turn,
                        "first": {"sequence": 1, "slot": 0},
                        "last": {"sequence": 4, "slot": 0},
                    },
                ],
                status,
            )
            assert changes is not None
            main, expanded = changes["scope_changes"]
            assert "turn:e1" in main["item_keys"]  # First retained item has slot 1.
            assert "e2" in main["removed_keys"] and "e2" not in main["item_keys"]
            assert "e2" in expanded["item_keys"]
            assert "e3" in main["item_keys"] and "e3" in expanded["removed_keys"]
            assert len([row for row in changes["upserts"] if row["key"] == "e2"]) == 1
        finally:
            live.close()


def test_reconciliation_restores_a_pruned_summary_when_latest_pointer_moves_backward() -> None:
    rows = [
        prompt(),
        event(
            2,
            "prompt_delivery_uncertain",
            {
                "sender_label": "owner",
                "mode": "queue",
                "text": "Uncertain",
                "sender_message_id": "same",
            },
        ),
    ]
    with database(rows) as conn:
        assert (
            conn.execute(
                "SELECT rest_summary FROM conversation_view_rows WHERE sequence=1"
            ).fetchone()[0]
            is None
        )
    rows.append(
        event(
            3,
            "prompt",
            {
                "sender_label": "owner",
                "mode": "queue",
                "text": "",
                "sender_message_id": "same",
                "reconciles_sequence": 2,
            },
        )
    )
    with database(rows) as conn:
        assert (
            conn.execute(
                "SELECT latest_full_happened_sequence FROM conversation_view_state"
            ).fetchone()[0]
            == 1
        )
        summary = conn.execute(
            "SELECT rest_summary FROM conversation_view_rows WHERE sequence=1"
        ).fetchone()[0]
        assert json.loads(summary)["excerpt"] == "Work"


def test_paging_back_through_a_long_timeline_loses_no_item_and_repeats_none() -> None:
    """A page is chosen per union member now, so a member could silently drop items.

    Candidates come from several queries unioned together, and each is bounded to a page
    before the union so that no member has to produce the whole conversation. That bound
    is only sound because a page cannot contain more than a page from any one member. If
    it were ever lowered below the page size, or a member gained a second row per
    sequence, pages would start losing items with nothing else failing - the pages would
    still be the right size and still be in order.

    Existing coverage pages twice inside one scope, which cannot see this: it needs more
    than a page's worth from a single member. So this builds 150 turns, which puts both
    the row member and the turn-head member well past the bound, and walks the whole
    timeline.
    """
    events: list[dict[str, Any]] = []
    prompts: list[int] = []
    sequence = 0
    for turn in range(150):
        sequence += 1
        prompts.append(sequence)
        events.append(prompt(sequence))
        sequence += 1
        events.append(event(sequence, "agent_message", {"text": f"Answer {turn}"}))
        sequence += 1
        events.append(event(sequence, "turn_ended", {"ending": "completed", "error_summary": None}))

    with database(events) as conn:
        seen: list[str] = []
        order: list[tuple[int, int]] = []
        cursor = None
        for _ in range(100):
            page = read_page(conn, "fixture", "full", cursor=cursor)
            # Pages arrive oldest-first within the page, and we walk backwards.
            seen = [item["key"] for item in page["items"]] + seen
            order = [
                (item["order"]["sequence"], item["order"]["slot"]) for item in page["items"]
            ] + order
            cursor = page["older"]
            if cursor is None:
                break
        else:
            raise AssertionError("paging back through 150 turns never reached the beginning")

        assert len(seen) == len(set(seen)), "a page repeated an item the previous page had"
        assert order == sorted(order), "the walk did not come back in one ascending order"
        missing_prompts = [f"e{at}" for at in prompts if f"e{at}" not in set(seen)]
        assert missing_prompts == [], f"paging lost {len(missing_prompts)} prompts"
        missing_heads = [f"turn:e{at}" for at in prompts if f"turn:e{at}" not in set(seen)]
        assert missing_heads == [], f"paging lost {len(missing_heads)} turn heads"
