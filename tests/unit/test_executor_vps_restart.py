"""Restart discards capabilities while retaining exact native stop custody.

These use the existing SQLite fixture and command double, without a browser or
connected Mac host. Socket loss and command replay remain covered by transport tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from tests.support.executor_control import message_link
from tests.unit.test_executor_native_runtime import canonical_snapshot
from tests.unit.test_executor_request_deadlines import peer

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
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    ChildCommand,
    ChildInventory,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    ReconnectResult,
    WireMessage,
    WireMessageKind,
    encode_value,
)
from planner.executor.vps_transport import VpsExecutor


@pytest.mark.parametrize("stop_before_inventory", [False, True])
@pytest.mark.parametrize("reported_by_mac", [False, True])
def test_restart_closes_callback_and_waits_for_exact_stop_before_fresh_run(
    tmp_db: Connection, tmp_path: Path, stop_before_inventory: bool, reported_by_mac: bool
) -> None:
    """Gap: startup, inventory retirement, and admission must share one child fence."""
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    async def run() -> None:
        commands: list[ChildCommand] = []
        stop_entered, release_stop = asyncio.Event(), asyncio.Event()
        identity = ExecutorIdentity(
            PROTOCOL_VERSION,
            "a" * 40,
            SPOOL_FORMAT_VERSION,
            "22222222-2222-2222-2222-222222222222",
            1,
        )

        async def native(command: ChildCommand) -> CommandOutcome:
            # This is the first possible native emission. Both halves must exist.
            retained = tmp_db.execute(
                "SELECT executor_incarnation,child_id FROM mac_runs WHERE conversation_id='c'"
            ).fetchone()
            assert retained is not None
            assert tuple(retained) == (command.child.executor_incarnation, command.child.child_id)
            assert endpoint.runs.store.child_assignment(command.child) == (
                "ticket",
                "restart-ticket",
            )
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

        def core() -> tuple[VpsExecutor, SqliteProcessConversationSystem]:
            endpoint = VpsExecutor(path, RealClock())
            endpoint.runs.identity, endpoint.runs.ready = identity, True
            endpoint.runs.store.begin_connection(identity, 1)
            endpoint.runs.store.mark_ready(identity, 1)
            endpoint.runs.send_command = native  # type: ignore[method-assign]
            endpoint.runs._read_skill_snapshot = lambda start: canonical_snapshot()  # type: ignore[method-assign]
            system = SqliteProcessConversationSystem(
                store=ConversationStore(path),
                backend_child_factories={key: endpoint.runs for key in ConversationBackendKey},
                message_files=ConversationMessageFiles(path),
            )
            # Like the existing held-message fixture, this synthetic conversation
            # has a permanent native destination even after its old child retires.
            system._catalog_endpoint = cast(
                Any,
                SimpleNamespace(identity=identity, ready=True, owns_conversation=lambda _: True),
            )
            endpoint.bind_conversations(system)
            endpoint.runs.link = message_link(peer(), identity)
            return endpoint, system

        endpoint, first = core()
        await first.start_conversation(
            ConversationStartRequest(
                "c",
                "scripted",
                workspace_folder=tmp_path,
                role_materials=ConversationRoleMaterials(
                    "Worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "restart-ticket"))
                ),
            )
        )
        assert isinstance(
            await first.send("c", text_message_content("Original"), sender_label="Test"),
            PromptDeliveryStarted,
        )
        state = first._conversations["c"]
        assert state.running_turn is not None and state.child_event_sink is not None
        old = state.executor_child_reference
        assert old is not None
        token = state.running_turn.token
        await cast(BackendEventSink, state.child_event_sink).permission_ask_raised(
            token,
            BackendPermissionAsk(
                "allow", "Allow?", None, (PermissionAskOption("yes", "Yes", "allow_once"),)
            ),
        )
        await first.wait_until_quiescent()
        ask = AskReference(old, token, "allow", AskKind.permission)
        endpoint.runs.store.publish_ask(ask, ("yes",), 1)
        saved = endpoint.runs.store.choose_answer(ask, "yes", 1, 2)
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
            assert not endpoint.runs.store.answer_target_is_open(ask)
            assert not endpoint.runs.store.answer_is_deliverable(saved, identity)
            if stop_before_inventory:
                await restarted.kill("c")
                await asyncio.wait_for(stop_entered.wait(), 1)
            stopping_child = state.child
            before = len(commands)
            await restarted.send("c", text_message_content("Blocked"), sender_label="Test")
            assert len(commands) == before
            assert endpoint.runs.link is not None
            endpoint.runs.link.request = AsyncMock(  # type: ignore[method-assign]
                return_value=WireMessage(
                    WireMessageKind.reconnect_result,
                    "ask",
                    cast(
                        Any,
                        encode_value(
                            ReconnectResult(
                                identity,
                                (),
                                (ChildInventory(old, token, (ask,), ()),)
                                if reported_by_mac
                                else (),
                            )
                        ),
                    ),
                )
            )
            await endpoint.runs._reconnect(identity, endpoint.runs.link)
            await asyncio.wait_for(stop_entered.wait(), 1)
            if stop_before_inventory:
                assert state.child is stopping_child
            assert commands[-1].operation is ChildOperation.stop and commands[-1].child == old
            assert state.child_is_quarantined
            assert state.initial_shutdown_task is not None
            release_stop.set()
            assert await state.initial_shutdown_task
            assert endpoint.runs.store.has_closed_child_assignment(old)
            assert endpoint.runs.store.recorded_answer(ask) == saved
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


def test_assignment_rolls_back_if_run_cannot_be_saved(tmp_db: Connection) -> None:
    """Run custody and assignment must commit together before transport."""
    from planner.executor.store import ExecutorStore

    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    child = ChildReference("22222222-2222-2222-2222-222222222222", "child", "c")
    tmp_db.execute(
        "CREATE TRIGGER refuse_run BEFORE INSERT ON mac_runs "
        "BEGIN SELECT RAISE(ABORT,'run write failed'); END"
    )
    tmp_db.commit()
    with pytest.raises(Exception, match="run write failed"):
        ExecutorStore(path).register_child(child, "chief", "chief", 1)
    assert tmp_db.execute("SELECT COUNT(*) FROM executor_child_assignments").fetchone()[0] == 0
    assert tmp_db.execute("SELECT COUNT(*) FROM mac_runs").fetchone()[0] == 0
