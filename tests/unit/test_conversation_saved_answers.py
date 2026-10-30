"""Saved answers have process custody; only callback proof establishes application.

Reuse the conversation HTTP harness and projection. No browser or native provider is needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.unit.test_conversation_api import _Harness, _start
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.backend_event_contracts import AcceptedEvent, EventTaken
from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import AnswerFate
from planner.conversation.events import (
    AnswerAppliedEventPayload,
    PermissionAnsweredEventPayload,
    UserInputAnswer,
    UserInputAnsweredEventPayload,
)
from planner.conversation.lifecycle import LifecycleStatus
from planner.conversation.message_content import text_message_content
from planner.conversation.views.api import router as view_router
from planner.execution_identity import AskKind, AskReference, ChildReference
from planner.executor.protocol import (
    ChildCommand,
    ChildInventory,
    CommandDisposition,
    CommandOutcome,
)
from planner.executor.vps_remote_child import MacRemoteChild


@pytest.mark.parametrize("kind", list(AskKind))
def test_saved_choice_and_late_application_are_distinct_conversation_facts(
    tmp_db: sqlite3.Connection, kind: AskKind
) -> None:
    async def run() -> None:
        harness = _Harness(Path(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
        harness.app.include_router(view_router, prefix="/api/conversation")
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
            reference = ChildReference("original-mac", "original-child", "c")
            commands: list[ChildCommand] = []

            async def send(command: ChildCommand) -> CommandOutcome:
                return CommandOutcome(
                    command.command_id, command.child, CommandDisposition.succeeded, {}
                )

            proxy = MacRemoteChild(
                reference, send, lambda _: canonical_snapshot(), send_answer=commands.append
            )
            state.child = proxy
            state.executor_child_reference = reference
            response = await client.post(f"/api/conversation/conversations/c/{path}", json=body)
            assert response.json() == {"fate": "saved"}
            assert (
                await client.post(f"/api/conversation/conversations/c/{path}", json=body)
            ).json() == {"fate": "refused"}
            assert len(commands) == 1
            answer_id = commands[0].command_id
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

    asyncio.run(run())


@pytest.mark.parametrize("kind", list(AskKind))
def test_proxy_reconnect_admits_only_the_original_callback(kind: AskKind) -> None:
    async def run() -> None:
        reference = ChildReference("mac", "child", "c")
        token = TurnToken("c", 3)
        ask = AskReference(reference, token, "same-id", kind)
        queued: list[ChildCommand] = []

        async def unused(command: ChildCommand) -> CommandOutcome:
            raise AssertionError("saved answers must not wait for a command result")

        proxy = MacRemoteChild(
            reference, unused, lambda _: canonical_snapshot(), send_answer=queued.append
        )
        if kind is AskKind.permission:
            fate = await proxy.answer_permission_ask(
                "same-id", "allow", answer_id="answer", turn_token=token
            )
        else:
            fate = await proxy.answer_user_input(
                "same-id", (UserInputAnswer("q", ("yes",)),), answer_id="answer", turn_token=token
            )
        assert fate is AnswerFate.saved and len(queued) == 1
        command = queued[0]
        proxy.observe_callbacks(
            ChildInventory(
                reference,
                token,
                (ask,) if kind is AskKind.permission else (),
                ("same-id",) if kind is AskKind.user_input else (),
            )
        )
        assert proxy.can_emit_answer(command)
        proxy.observe_callbacks(
            ChildInventory(reference, replace(token, turn_number=4), (ask,), ("same-id",))
        )
        assert not proxy.can_emit_answer(command)
        replacement = MacRemoteChild(
            reference, unused, lambda _: canonical_snapshot(), send_answer=queued.append
        )
        replacement.observe_callbacks(ChildInventory(reference, token, (ask,), ("same-id",)))
        assert not replacement.saved_answers and not replacement.can_emit_answer(command)
        await proxy.detach()
        assert not proxy.saved_answers

    asyncio.run(run())
