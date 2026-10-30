"""Restart discards capabilities while retaining exact native stop custody.

These use the existing SQLite fixture and command double, without a browser or
connected Mac host. Socket loss and command replay remain covered by transport tests.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.backends.contracts import BackendEventSink, BackendPermissionAsk
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryStarted,
)
from planner.conversation.events import PermissionAskOption
from planner.conversation.lifecycle import LifecycleKind, LifecycleStatus
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AskKind,
    AskReference,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    ExecutorIdentity,
    ReconciliationInventory,
    WireMessage,
    WireMessageKind,
)
from planner.executor.vps import ExecutorEndpoint


@pytest.mark.parametrize("stop_before_inventory", [False, True])
def test_restart_closes_callback_and_waits_for_exact_stop_before_fresh_run(
    tmp_db: Connection, tmp_path: Path, stop_before_inventory: bool
) -> None:
    """Gap: startup, inventory retirement, and admission must share one child fence."""
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    async def run() -> None:
        commands: list[ChildCommand] = []
        stop_entered, release_stop = asyncio.Event(), asyncio.Event()
        identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "mac", 1)

        async def native(command: ChildCommand) -> CommandOutcome:
            # This is the first possible native emission. Both halves must exist.
            retained = tmp_db.execute(
                "SELECT child_json,turn_json FROM executor_core_turns WHERE conversation_id='c'"
            ).fetchone()
            assert retained is not None
            assert json.loads(retained[0]) == encode_value(command.child)
            assert endpoint.store.child_assignment(command.child) == ("chief", "chief")
            commands.append(command)
            if command.operation is ChildOperation.stop:
                stop_entered.set()
                await release_stop.wait()
            return CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.succeeded,
                {"composed_content_delivered": True},
            )

        def core() -> tuple[ExecutorEndpoint, SqliteProcessConversationSystem]:
            endpoint = ExecutorEndpoint(path, RealClock())
            endpoint.identity, endpoint.ready = identity, True
            endpoint.store.begin_connection(identity, 1)
            endpoint.store.mark_ready(identity, 1)
            endpoint.factory.send_command = native
            endpoint.factory.skill_snapshot_supplier = lambda start: canonical_snapshot()
            endpoint.send_new_command = native  # type: ignore[method-assign]
            system = SqliteProcessConversationSystem(
                store=ConversationStore(path),
                backend_child_factories={key: endpoint.factory for key in ConversationBackendKey},
                message_files=ConversationMessageFiles(path),
            )
            # Like the existing held-message fixture, this synthetic conversation
            # has a permanent native destination even after its old child retires.
            system._catalog_endpoint = cast(
                Any,
                SimpleNamespace(identity=identity, ready=True, owns_conversation=lambda _: True),
            )
            endpoint.runtime = SimpleNamespace(system=system)
            endpoint.writer = cast(Any, Mock(drain=AsyncMock()))
            return endpoint, system

        endpoint, first = core()
        await first.start_conversation(
            ConversationStartRequest(
                "c",
                "scripted",
                workspace_folder=tmp_path,
                role_materials=ConversationRoleMaterials("Chief", (("PLAN_ACTOR", "chief"),)),
            )
        )
        assert isinstance(
            await first.send("c", text_message_content("Original"), sender_label="Test"),
            PromptDeliveryStarted,
        )
        state = first._conversations["c"]
        assert state.running_turn is not None and state.child_event_sink is not None
        old = state.executor_child_reference
        token = state.running_turn.token
        await cast(BackendEventSink, state.child_event_sink).permission_ask_raised(
            token,
            BackendPermissionAsk(
                "allow", "Allow?", None, (PermissionAskOption("yes", "Yes", "allow_once"),)
            ),
        )
        await first.wait_until_quiescent()
        metadata = first._executor_turn_metadata(state, state.running_turn)
        assert metadata is not None
        await first._store.save_executor_turn("c", metadata)
        ask = AskReference(old, token, "allow", AskKind.permission)
        endpoint.store.publish_ask(ask, ("yes",), 1)
        saved = endpoint.store.choose_answer(ask, "yes", 1, 2)
        await first.shutdown()
        identity = replace(identity, connection_generation=2)
        endpoint, restarted = core()
        try:
            state = restarted._conversations["c"]
            assert not await restarted.is_running("c")
            assert not await restarted.pending_permission_ask_ids("c")
            assert state.reserved_turn is None and state.child_is_quarantined
            facts = await restarted._store.lifecycle_facts("c")
            assert any(
                f.kind is LifecycleKind.turn and f.status is LifecycleStatus.unavailable
                for f in facts
            )
            assert not endpoint.store.answer_target_is_open(ask)
            assert not endpoint.store.answer_is_deliverable(saved, identity)
            if stop_before_inventory:
                await restarted.kill("c")
                await asyncio.wait_for(stop_entered.wait(), 1)
            stopping_child = state.child
            before = len(commands)
            await restarted.send("c", text_message_content("Blocked"), sender_label="Test")
            assert len(commands) == before
            assert endpoint.writer is not None
            await endpoint._handle_message(
                WireMessage(
                    WireMessageKind.reconcile_inventory,
                    "inventory",
                    cast(
                        Any,
                        encode_value(
                            ReconciliationInventory(
                                identity, (ChildInventory(old, token, (ask,), ()),), (), ()
                            )
                        ),
                    ),
                ),
                identity,
                endpoint.writer,
            )
            await asyncio.wait_for(stop_entered.wait(), 1)
            if stop_before_inventory:
                assert state.child is stopping_child
            assert commands[-1].operation is ChildOperation.stop and commands[-1].child == old
            assert state.child_is_quarantined
            assert state.initial_shutdown_task is not None
            release_stop.set()
            assert await state.initial_shutdown_task
            assert endpoint.store.has_closed_child_assignment(old)
            assert endpoint.store.recorded_answer(ask) == saved
            assert not state.child_is_quarantined
            await restarted.wait_until_quiescent()
            assert await restarted.is_running("c")
            assert state.executor_child_reference != old
            assert [c.operation for c in commands] == [
                ChildOperation.start,
                ChildOperation.write_prompt,
                ChildOperation.stop,
                ChildOperation.start,
                ChildOperation.write_prompt,
            ]
        finally:
            release_stop.set()
            await restarted.shutdown()

    asyncio.run(run())


def test_assignment_rolls_back_if_retained_turn_cannot_be_saved(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a failing turn write must never leave a separately committed assignment."""
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    store = ConversationStore(path)
    child = ChildReference("mac", "child", "c")
    metadata = (json.dumps(encode_value(child)), '{"conversation_id":"c","turn_number":1}', "{}")

    def fail(*arguments: object) -> None:
        raise RuntimeError("turn write failed")

    monkeypatch.setattr(store, "save_executor_turn_in_transaction", fail)
    with pytest.raises(RuntimeError, match="turn write failed"):
        asyncio.run(
            store.transition_lifecycle(
                "c", (), executor_turn=metadata, assigned_child=(child, CHIEF_PRINCIPAL)
            )
        )
    assert tmp_db.execute("SELECT COUNT(*) FROM executor_child_assignments").fetchone()[0] == 0
    assert tmp_db.execute("SELECT COUNT(*) FROM executor_core_turns").fetchone()[0] == 0
