"""Exact-child stop fences delayed journal writes without granting live authority.

A closed assignment refuses the child's later items on the VPS, and a command put for
it is never committed, even when the close lands while the put is on its way.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from sqlite3 import Connection
from typing import Any

import pytest
from tests.support.executor_presence import mac_identity, make_current
from tests.unit.test_executor_native_runtime import ScriptedChild, canonical_snapshot
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

import planner.executor.vps_mailbox as vps_mailbox
from planner.conversation.backends.contracts import BackendSpawnFailed, PromptWriteRefused
from planner.conversation.contracts import AnswerFate, PromptDeliveryMode
from planner.conversation.message_content import text_message_content
from planner.core.clock import RealClock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.execution_identity import ExecutorIdentity, TurnToken
from planner.executor.mac_outbox import MacOutbox, OutboxEntry
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    EnvelopeKind,
    ReceiptDisposition,
    StartRun,
    as_object,
    decode_command_arguments,
)
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_transport import VpsExecutor
from planner.tickets import data
from planner.tickets.contracts import Ticket

SETTLES = 5.0


def ticket(conn: Connection) -> Ticket:
    return data.create_ticket(
        conn,
        title="Exact-child retirement",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="test",
        employee_launch_reasoning_effort="high",
    )


def endpoint(conn: Connection, identity: ExecutorIdentity) -> VpsExecutor:
    """The VPS half with identity as the current Mac process."""
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    make_current(path, identity)
    return VpsExecutor(path, RealClock())


def _rows(db_path: str) -> list[str]:
    connection = connect(db_path)
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT operation FROM executor_commands ORDER BY command_sequence"
            )
        ]
    finally:
        connection.close()


@pytest.mark.parametrize("bad_case", range(4))
def test_retired_delayed_start_refuses_without_live_child_authority(
    tmp_db: Connection,
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    bad_case: int,
) -> None:
    async def run() -> None:
        from dataclasses import replace

        from planner.conversation.backends.contracts import TurnToken
        from planner.executor.protocol import (
            BackendEvent,
            BackendEventMethod,
            CliWrite,
            CommandDisposition,
        )

        t = ticket(tmp_db)
        registry, spool, native, start = native_registry
        identity = mac_identity(registry.incarnation)
        remote = endpoint(tmp_db, identity)
        remote.runs.store.register_child(start.child, "ticket", t.id, 3)
        stop = ChildCommand(
            "stop",
            start.child,
            ChildOperation.stop,
            None,
            decode_command_arguments(ChildOperation.stop, {}),
        )
        assert (await registry.run(stop)).disposition is CommandDisposition.succeeded
        (stopped,) = spool.pending()
        spool.acknowledge(await remote.inbox.accept(stopped, identity))
        remote.runs.store.close_child(start.child, 4)
        outcome = await registry.run(start)
        assert outcome.disposition is CommandDisposition.refused
        assert native.starts == 0
        assert spool.pending() == ()
        assert registry._commands[start.command_id].outcome == outcome
        assert await registry.run(start) == outcome
        # Its direct refusal grants no authority to publish fresh child output.
        bad_payloads = [
            (
                EnvelopeKind.child_stopped,
                replace(outcome, command_id="unknown", disposition=CommandDisposition.uncertain),
            ),
            (
                EnvelopeKind.child_stopped,
                replace(outcome, command_id="answer", result={"answer_application": {}}),
            ),
            (EnvelopeKind.cli_write, CliWrite("ticket.propose", {}, start.child, None)),
            (
                EnvelopeKind.backend_event,
                BackendEvent(
                    start.child,
                    BackendEventMethod.turn_ended,
                    TurnToken(start.child.conversation_id, 1),
                    {},
                ),
            ),
        ]
        kind, payload = bad_payloads[bad_case]
        row = registry.outbox.add(
            OutboxEntry(
                conversation_id=start.child.conversation_id, kind=kind, payload=as_object(payload)
            )
        )
        receipt = await remote.inbox.accept(row, identity)
        assert receipt.disposition is ReceiptDisposition.refused
        assert remote.runs.store.child_assignment(start.child) is None
        await remote.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "path", ["start_after_close", "prompt_racing_close", "answer_racing_close"]
)
def test_closed_assignment_refuses_a_put_even_when_the_close_lands_during_it(
    tmp_db: Connection,
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """The close can commit after the caller looked: the put reads the assignment in its
    own write, so nothing reaches the mailbox and the Mac never sees the command."""

    async def run() -> None:
        registry, _spool, native, start = native_registry
        identity = mac_identity(registry.incarnation)
        remote = endpoint(tmp_db, identity)
        t = ticket(tmp_db)
        remote.runs.store.register_child(start.child, "ticket", t.id, 3)

        class Conversations:
            async def emit_answer(
                self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, object]]
            ) -> None:
                await emit()

        remote.runs.conversations = Conversations()  # type: ignore[assignment]
        proxy = MacRemoteChild(start.child, remote.runs, lambda _: canonical_snapshot())
        try:
            if path == "start_after_close":
                remote.runs.store.close_child(start.child, 4)
                assert isinstance(start.arguments, StartRun)
                with pytest.raises(BackendSpawnFailed):
                    await proxy.start(start.arguments.resolved_start, vendor_session_cursor=None)
            else:
                entered, release = threading.Event(), threading.Event()
                original = vps_mailbox.SqliteCommandMailbox._insert_child_command

                def gated(self: Any, *args: Any) -> Any:
                    entered.set()
                    assert release.wait(SETTLES)
                    return original(self, *args)

                monkeypatch.setattr(
                    vps_mailbox.SqliteCommandMailbox, "_insert_child_command", gated
                )
                turn = TurnToken(start.child.conversation_id, 1)
                content = text_message_content("late")

                async def put() -> object:
                    if path == "prompt_racing_close":
                        return await proxy.write_prompt(
                            turn,
                            content,
                            sender_label="owner",
                            sender_content=content,
                            mode=PromptDeliveryMode.queue,
                            model_change=None,
                            reasoning_effort_change=None,
                        )
                    return await proxy.answer_permission_ask(
                        "q", "yes", answer_id="answer", turn_token=turn
                    )

                putting = asyncio.create_task(put())
                try:
                    assert await asyncio.to_thread(entered.wait, SETTLES)
                    await asyncio.to_thread(remote.runs.store.close_child, start.child, 4)
                finally:
                    release.set()
                if path == "prompt_racing_close":
                    with pytest.raises(PromptWriteRefused):
                        await asyncio.wait_for(putting, SETTLES)
                else:
                    assert await asyncio.wait_for(putting, SETTLES) is AnswerFate.refused
            assert _rows(remote.db_path) == []
            assert native.starts == native.prompts == 0
            assert native.answers == []
        finally:
            await remote.close()

    asyncio.run(run())
