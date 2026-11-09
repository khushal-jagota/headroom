"""Saved answers have process custody; only callback proof establishes application.

A saved answer is a mailbox row, inserted inside the core's emit_answer, so only while
its turn is live. Reuse the conversation HTTP harness and projection over the real
VpsExecutor mailbox. No browser, Mac or native provider is needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.executor_presence import mac_identity, make_current
from tests.unit.test_conversation_api import _Harness, _start

from planner.conversation.backend_event_contracts import AcceptedEvent, EventTaken
from planner.conversation.contracts import AnswerFate
from planner.conversation.events import (
    AnswerAppliedEventPayload,
    PermissionAnsweredEventPayload,
    UserInputAnsweredEventPayload,
)
from planner.conversation.lifecycle import LifecycleStatus
from planner.conversation.message_content import text_message_content
from planner.conversation.views.api import router as view_router
from planner.core.clock import RealClock
from planner.core.db import connect
from planner.execution_identity import AskKind, ChildReference
from planner.executor.store import ExecutorStore
from planner.executor.vps_transport import VpsExecutor


def _answer_rows(db_path: str) -> list[tuple[str, str]]:
    connection = connect(db_path)
    try:
        return [
            (row[0], row[1])
            for row in connection.execute(
                "SELECT command_id,operation FROM executor_commands "
                "WHERE operation LIKE 'answer_%' ORDER BY command_sequence"
            )
        ]
    finally:
        connection.close()


@pytest.mark.parametrize("kind", list(AskKind))
def test_saved_choice_and_late_application_are_distinct_conversation_facts(
    tmp_db: sqlite3.Connection, kind: AskKind
) -> None:
    async def run() -> None:
        db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
        harness = _Harness(Path(db_path))
        harness.app.include_router(view_router, prefix="/api/conversation")
        identity = mac_identity()
        make_current(db_path, identity)
        endpoint = VpsExecutor(db_path, RealClock())
        endpoint.bind_conversations(harness.system)
        async with harness.client() as client:

            async def projected_answer() -> dict[str, Any]:
                page = (await client.get("/api/conversation/conversations/c/view/pages")).json()
                return cast(
                    dict[str, Any],
                    next(
                        row
                        for row in page["items"]
                        if row.get("row_kind")
                        == ("permission_ask" if kind is AskKind.permission else "user_input")
                    ),
                )

            await _start(client, "c")
            await harness.system.send("c", text_message_content("work"), sender_label="owner")
            body: dict[str, object]
            if kind is AskKind.permission:
                request_id = await harness.raise_permission_ask("c")
                body = {"ask_id": request_id, "option_id": "allow-once"}
                path = "permission-answers"
            else:
                request = await harness.request_user_input("c")
                request_id = request.request_id
                body = {
                    "request_id": request_id,
                    "answers": {"scope": {"answers": ["Backend"]}, "timing": {"answers": ["Now"]}},
                }
                path = "user-input-answers"
            state = harness.system._conversations["c"]
            running = state.running_turn
            assert running is not None
            token = running.token
            provider_id = (
                running.permission_provider_ids
                if kind is AskKind.permission
                else running.user_input_provider_ids
            )[request_id]
            reference = ChildReference(identity.executor_incarnation, "original-child", "c")
            ExecutorStore(db_path).register_child(reference, "ticket", "test-ticket", 1)
            proxy = endpoint.runs.recover(reference)
            assert proxy is not None
            state.child = proxy
            state.retained_child_reference = reference
            response = await client.post(f"/api/conversation/conversations/c/{path}", json=body)
            assert response.json() == {"fate": "saved"}
            assert (
                await client.post(f"/api/conversation/conversations/c/{path}", json=body)
            ).json() == {"fate": "refused"}
            rows = _answer_rows(db_path)
            assert len(rows) == 1 and rows[0][1] == (
                "answer_permission_ask" if kind is AskKind.permission else "answer_user_input"
            )
            answer_id = rows[0][0]
            events = await harness.store.read_events_after("c", 0)
            choice = next(
                e.payload
                for e in events
                if isinstance(
                    e.payload, (PermissionAnsweredEventPayload, UserInputAnsweredEventPayload)
                )
            )
            assert choice.fate is AnswerFate.saved and choice.answer_id == answer_id
            assert not any(isinstance(e.payload, AnswerAppliedEventPayload) for e in events)
            view = (await client.get("/api/conversation/conversations/c")).json()
            assert "executor_context" not in view and "executor_asks" not in view
            assert view["pending_permission_ask"] is None and view["pending_user_input"] is None
            saved_row = await projected_answer()
            assert saved_row["body"]["value"]["answer_fate"] == "saved"
            await harness.system.interrupt("c")
            # Once its turn has ended, no answer row can be inserted for it.
            late = (
                await proxy.answer_permission_ask(
                    provider_id, "allow-once", answer_id="late", turn_token=token
                )
                if kind is AskKind.permission
                else await proxy.answer_user_input(
                    provider_id, (), answer_id="late", turn_token=token
                )
            )
            assert late is AnswerFate.refused
            assert [row[0] for row in _answer_rows(db_path)] == [answer_id]
            ended_row = await projected_answer()
            assert ended_row["body"]["value"]["answer_fate"] == "unconfirmed"
            assert ended_row["changed_through_sequence"] > saved_row["changed_through_sequence"]
            facts = await harness.store.lifecycle_facts("c")
            assert (
                next(f for f in facts if f.identity == request_id).status
                is LifecycleStatus.unavailable
            )

            def in_commit(connection: sqlite3.Connection, take: EventTaken) -> AcceptedEvent:
                result = take()
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS application_receipt (answer_id TEXT PRIMARY KEY)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO application_receipt VALUES (?)", (answer_id,)
                )
                return AcceptedEvent(result, True)

            result = await harness.system.take_answer_application(
                token, answer_id, provider_id, kind.value, in_commit
            )
            assert result.taken
            applied_row = await projected_answer()
            assert applied_row["body"]["value"]["answer_fate"] == "applied"
            assert applied_row["changed_through_sequence"] > ended_row["changed_through_sequence"]
            await harness.system.take_answer_application(
                token, answer_id, provider_id, kind.value, in_commit
            )
            events = await harness.store.read_events_after("c", 0)
            assert sum(isinstance(e.payload, AnswerAppliedEventPayload) for e in events) == 1
            facts = await harness.store.lifecycle_facts("c")
            assert (
                next(f for f in facts if f.identity == request_id).status
                is LifecycleStatus.unavailable
            )
            assert not await harness.system.has_pending_permission_ask("c")
            assert not await harness.system.has_pending_user_input("c")
        await harness.runtime.shutdown()
        await endpoint.close()

    asyncio.run(run())
