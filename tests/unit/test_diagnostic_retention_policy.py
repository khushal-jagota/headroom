"""Destructive selection risks: independent expected keys, ownership, and pair closure."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_db import _migration_tree_through

from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.retention.contracts import (
    ConversationRetentionInput,
    ManagerOwner,
    RetentionEvent,
    TicketOwner,
)
from planner.conversation.retention.data import build_report, read_report
from planner.conversation.retention.logic.rules import classify_conversation, event_fingerprint
from planner.core.db import connect, create_schema


def event(sequence: int, kind: str, payload: dict[str, object], at: int = 1) -> RetentionEvent:
    return RetentionEvent("c", sequence, kind, json.dumps(payload, ensure_ascii=False), at)


def source(*events: RetentionEvent) -> ConversationRetentionInput:
    return ConversationRetentionInput(
        "c",
        '[["PLAN_ACTOR","worker"],["PLAN_TICKET_ID","t"]]',
        (TicketOwner("t", "done", "none"),),
        (),
        (),
        tuple(events),
        (),
        0,
        0,
        ("t",),
        (),
    )


def selected(value: ConversationRetentionInput) -> set[int]:
    return {
        candidate.sequence for candidate in classify_conversation(value, cutoff_unix=10).candidates
    }


def test_cutoff_controls_latest_plan_timestamp_ties_and_markers_are_not_expendable() -> None:
    value = source(
        event(1, "prompt", {"text": "work", "sender_label": "owner", "mode": "queue"}),
        event(2, "agent_message", {"text": "é"}),
        event(3, "agent_message", {"text": "on boundary"}, 10),
        event(4, "plan_updated", {"entries": []}),
        event(5, "plan_updated", {"entries": []}),
        event(6, "context_compacted", {}),
        event(7, "token_usage", {}),
        event(8, "turn_ended", {"ending": "completed"}),
        event(9, "agent_message", {"text": "timestamp tie"}, 20),
        event(10, "token_usage", {}, 20),
    )
    value = replace(value, protected_sequences=(6,))
    decision = classify_conversation(value, cutoff_unix=10)
    assert selected(value) == {2, 4, 7}
    assert not decision.hold_reasons
    candidate = next(row for row in decision.candidates if row.sequence == 2)
    assert candidate.payload_bytes == len(value.events[1].payload.encode("utf-8"))
    assert candidate.row_sha256 == event_fingerprint(value.events[1])
    assert event_fingerprint(value.events[1]) != event_fingerprint(
        replace(value.events[1], created_at=2)
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"tickets": (TicketOwner("t", "needs_implementation", "none"),)}, "unfinished_ticket"),
        ({"tickets": (TicketOwner("t", "done", "out"),)}, "worker_claim"),
        (
            {"tickets": (TicketOwner("t", "done", "none"), TicketOwner("old", "backlog", "none"))},
            "unfinished_ticket",
        ),
        ({"managers": (ManagerOwner("o", False),)}, "unsettled_manager"),
        ({"identity_json": '[["PLAN_ACTOR","chief"]]'}, "chief"),
        ({"agent_keys": ("chief_of_staff",)}, "chief"),
        (
            {"identity_json": '[["PLAN_ACTOR","worker"],["PLAN_TICKET_ID","missing"]]'},
            "conflicting_ownership",
        ),
        (
            {"identity_json": '[["PLAN_ACTOR","worker"],["PLAN_ACTOR","worker"]]'},
            "unknown_identity",
        ),
        ({"identity_json": '{"PLAN_ACTOR":"worker"}'}, "unknown_identity"),
        ({"identity_json": "[]", "tickets": ()}, "unknown_ownership"),
        ({"linked_ticket_ids": ()}, "conflicting_ownership"),
    ],
)
def test_every_owner_must_qualify_and_identity_is_fail_closed(
    change: dict[str, Any],
    reason: str,
) -> None:
    value = source(
        event(1, "agent_message", {"text": "old"}),
        event(2, "turn_ended", {"ending": "completed"}, 20),
    )
    decision = classify_conversation(replace(value, **change), cutoff_unix=10)
    assert not decision.candidates
    assert reason in decision.hold_reasons


@pytest.mark.parametrize(
    "problem",
    [
        event(3, "future_kind", {}),
        RetentionEvent("c", 3, "agent_message", '{"text":"first","text":"duplicate"}', 1),
        event(3, "agent_message", {"text": 4}),
        event(3, "token_usage", {"input_tokens": True}),
        event(3, "context_compacted", {"future_fact": 1}),
        event(3, "user_input_answered", {"request_id": "r", "answers": {"q": 42}}),
    ],
)
def test_unknown_or_malformed_rows_hold_every_diagnostic(problem: RetentionEvent) -> None:
    value = source(
        event(1, "agent_message", {"text": "old"}),
        problem,
        event(4, "turn_ended", {"ending": "completed"}, 20),
    )
    decision = classify_conversation(value, cutoff_unix=10)
    assert not decision.candidates
    assert "unknown_or_malformed_event" in decision.hold_reasons


def test_tool_pairs_are_atomic_and_ambiguous_repeated_ids_stay() -> None:
    def start(seq: int, name: str, at: int = 1) -> RetentionEvent:
        return event(
            seq,
            "tool_call_started",
            {
                "tool_call_id": name,
                "title": "tool",
                "tool_kind": "exec",
                "detail": None,
            },
            at,
        )

    def finish(seq: int, name: str, at: int = 1) -> RetentionEvent:
        return event(
            seq,
            "tool_call_finished",
            {
                "tool_call_id": name,
                "tool_call_status": "completed",
                "detail": None,
            },
            at,
        )

    value = source(
        start(1, "complete"),
        finish(2, "complete"),
        start(3, "unmatched"),
        start(4, "boundary"),
        finish(5, "boundary", 10),
        start(6, "protected"),
        finish(7, "protected"),
        start(8, "repeated"),
        finish(9, "repeated"),
        start(10, "repeated"),
        finish(11, "repeated"),
        event(12, "turn_ended", {"ending": "completed"}, 20),
    )
    assert selected(replace(value, protected_sequences=(7,))) == {1, 2}


@pytest.mark.parametrize(
    "attention,reason",
    [
        (
            event(3, "prompt", {"text": "work", "sender_label": "owner", "mode": "queue"}),
            "open_turn",
        ),
        (
            event(3, "permission_asked", {"ask_id": "p", "title": "ask", "options": []}),
            "unresolved_request",
        ),
        (
            event(3, "user_input_requested", {"request_id": "r", "questions": []}),
            "unresolved_request",
        ),
        (
            event(
                3,
                "prompt_delivery_uncertain",
                {
                    "text": "work",
                    "sender_label": "owner",
                    "mode": "queue",
                },
            ),
            "uncertain_delivery",
        ),
        (event(3, "turn_ended", {"ending": "failed"}), "unacknowledged_error"),
    ],
)
def test_unsettled_attention_and_delivery_hold_the_whole_conversation(
    attention: RetentionEvent,
    reason: str,
) -> None:
    value = source(event(1, "agent_message", {"text": "old"}), attention)
    decision = classify_conversation(value, cutoff_unix=10)
    assert not decision.candidates
    assert reason in decision.hold_reasons


def test_explicit_receipt_reconciliation_releases_uncertainty_without_removing_receipts() -> None:
    value = source(
        event(
            1,
            "prompt_delivery_uncertain",
            {
                "text": "work",
                "sender_label": "owner",
                "mode": "queue",
            },
        ),
        event(2, "agent_message", {"text": "diagnostic"}),
        event(
            3,
            "prompt",
            {
                "text": "work",
                "sender_label": "owner",
                "mode": "queue",
                "reconciles_sequence": 1,
            },
        ),
        event(4, "turn_ended", {"ending": "completed"}, 20),
    )
    assert selected(value) == {2}


def test_report_reads_without_initialization_and_binds_source_schema_and_database_identity(
    tmp_db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_path = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    assert read_report(current_path, as_of_unix=2_000_000).database_path == str(current_path)
    tmp_db.execute("UPDATE alembic_version SET version_num = 'unknown_future_revision'")
    tmp_db.commit()
    with pytest.raises(ValueError, match="Unsupported diagnostic retention schema revision"):
        read_report(current_path, as_of_unix=2_000_000)
    # Build the supported schema rather than relabeling an incompatible current database.
    _migration_tree_through(tmp_path, monkeypatch, "owner_message_preview")
    path = tmp_path / "supported-report.db"
    with closing(connect(str(path))) as supported_db:
        create_schema(supported_db)
        first = read_report(path, as_of_unix=2_000_000)
        assert first == read_report(path, as_of_unix=2_000_000)
        supported_db.execute("CREATE TABLE retention_test_fact (fact TEXT NOT NULL)")
        supported_db.execute("INSERT INTO retention_test_fact VALUES ('first')")
        supported_db.commit()
        second = read_report(path, as_of_unix=2_000_000)
        assert first.schema_fingerprint != second.schema_fingerprint
        supported_db.execute("UPDATE retention_test_fact SET fact='changed'")
        supported_db.commit()
        third = read_report(path, as_of_unix=2_000_000)
        assert second.source_fingerprint != third.source_fingerprint
        assert second.schema_fingerprint == third.schema_fingerprint
        assert second.database_identity == third.database_identity
        with pytest.raises(ValueError, match="snapshot"):
            build_report(supported_db, database_path=path, as_of_unix=2_000_000)
        supported_db.execute("BEGIN")
        with pytest.raises(ValueError, match="path differs"):
            build_report(supported_db, database_path=path.parent, as_of_unix=2_000_000)
        supported_db.rollback()


@pytest.mark.parametrize(
    "fact",
    [
        LifecycleFact(
            LifecycleKind.turn, "turn", 1, LifecycleOperation.turn, LifecycleStatus.running
        ),
        LifecycleFact(
            LifecycleKind.request, "ask", 1, LifecycleOperation.permission, LifecycleStatus.pending
        ),
        LifecycleFact(
            LifecycleKind.attempt, "attempt", 1, LifecycleOperation.prompt, LifecycleStatus.unknown
        ),
        LifecycleFact(
            LifecycleKind.attempt, "invalid", 1, LifecycleOperation.prompt, LifecycleStatus.accepted
        ),
    ],
)
def test_durable_unsettled_or_invalid_lifecycle_facts_block_closed_event_history(
    fact: LifecycleFact,
) -> None:
    value = source(
        event(1, "agent_message", {"text": "old"}),
        event(2, "turn_ended", {"ending": "completed"}, 20),
    )
    decision = classify_conversation(replace(value, lifecycle_facts=(fact,)), cutoff_unix=10)
    assert not decision.candidates
    assert set(decision.hold_reasons) & {"unsettled_lifecycle", "unknown_lifecycle_fact"}


def test_report_refuses_an_unfamiliar_projection_payload_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migration_tree_through(tmp_path, monkeypatch, "owner_message_preview")
    path = tmp_path / "supported-projection.db"
    with closing(connect(str(path))) as conn:
        create_schema(conn)
        conn.execute("CREATE TABLE conversation_view_future_payloads (payload TEXT)")
        conn.commit()
        with pytest.raises(ValueError, match="projection schema"):
            read_report(path, as_of_unix=2_000_000)
