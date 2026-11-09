"""Exact-child stop fences delayed journal writes without granting live authority."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from tests.support.executor_control import MessageStream, message_link
from tests.unit.test_executor_native_runtime import ScriptedChild
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.core.clock import Clock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity, TurnToken
from planner.executor.link import Link
from planner.executor.mac_outbox import MacOutbox, OutboxEntry
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    AcceptanceReceipt,
    AnswerPermission,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    ReceiptDisposition,
    StopRun,
    WireMessageKind,
    as_object,
    decode_command_arguments,
    decode_message,
    type_adapter,
)
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_runs import _PendingCommand
from planner.executor.vps_transport import VpsExecutor
from planner.tickets import data
from planner.tickets.contracts import Ticket

IDENTITY = ExecutorIdentity(
    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "ca27d941-1e77-4ac9-a102-e0ac14b51626", 1
)


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


def endpoint(conn: Connection, *, identity: ExecutorIdentity = IDENTITY) -> VpsExecutor:
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    result = VpsExecutor(path, cast(Clock, SimpleNamespace()))
    result.runs.identity = identity
    result.runs.link = message_link(MessageStream(), identity)
    result.runs.ready = True
    result.runs.store.begin_connection(identity)
    result.runs.store.mark_ready(identity)
    return result


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
        identity = replace(IDENTITY, executor_incarnation=registry.incarnation)
        remote = endpoint(tmp_db, identity=identity)
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

    asyncio.run(run())


def test_accepted_stop_fences_unvisited_commands_behind_an_unrelated_answer(
    tmp_db: Connection,
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gap: replay can wait outside the writer lock before it visits an old start."""
    async def run() -> None:
        registry, _spool, native, start = native_registry
        identity = replace(IDENTITY, executor_incarnation=registry.incarnation)
        remote = endpoint(tmp_db, identity=identity)
        runs = remote.runs
        assert runs.link is not None
        link = runs.link
        stream = cast(MessageStream, link._stream)
        t = ticket(tmp_db)
        runs.store.register_child(start.child, "ticket", t.id, 3)
        other = ChildReference(identity.executor_incarnation, str(uuid4()), "c_other")
        runs.store.register_child(other, "ticket", t.id, 3)
        earlier = ChildCommand(
            "earlier_answer", other, ChildOperation.answer_permission_ask,
            TurnToken(other.conversation_id, 1), AnswerPermission(ask_id="q", option_id="yes"),
        )
        stale_answer = replace(
            earlier, command_id="stale_answer", child=start.child,
            turn=TurnToken(start.child.conversation_id, 1),
        )
        pending_stop = ChildCommand(
            "existing_stop", start.child, ChildOperation.stop, None, StopRun()
        )
        commands = (earlier, start, stale_answer, pending_stop)
        runs.pending_commands = {
            command.command_id: _PendingCommand(command, asyncio.get_running_loop().create_future())
            for command in commands
        }
        entered, release, never = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Conversations:
            async def emit_answer(
                self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, None]]
            ) -> None:
                if turn.conversation_id == other.conversation_id:
                    entered.set()
                    await release.wait()
                else:
                    await emit()

            async def confirm_remote_child_termination(self, child: ChildReference) -> None:
                await never.wait()

        runs.conversations = cast(Any, Conversations())
        for reference in (other, start.child):
            runs.children[reference.child_id] = cast(MacRemoteChild, SimpleNamespace(
                reference=reference, saved_answers={}, can_emit_answer=lambda command: True,
            ))

        async def reconnect(peer: ExecutorIdentity, writer: Link) -> set[str]:
            return {command.command_id for command in commands}

        monkeypatch.setattr(runs, "_reconnect", reconnect)
        replaying = asyncio.create_task(runs.link_up(link, "ready"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            outcome = CommandOutcome("update_stop", start.child, CommandDisposition.succeeded, {})
            receipt = AcceptanceReceipt("sender", "stopped", ReceiptDisposition.accepted, {
                "recorded": True, "stop_command_id": outcome.command_id,
                "stopped_child": as_object(start.child),
            }, 4)
            await asyncio.wait_for(runs.accept_stop_receipt(outcome, receipt), 1)
            assert runs.store.has_closed_child_assignment(start.child)
            for command in (start, stale_answer):
                settled = runs.pending_commands[command.command_id].future.result()
                assert settled.disposition is CommandDisposition.uncertain
            assert not runs.pending_commands[pending_stop.command_id].future.done()
            # Model the candidate's proved FIFO/marker gate now being open.
            registry.begin_planned_replay()
            registry.finish_planned_replay()
            registry.resume_after_cancelled_update()
            release.set()
            await asyncio.wait_for(replaying, 1)
            emitted = [
                type_adapter(ChildCommand).validate_python(message.payload)
                for body in stream.sent
                if (message := decode_message(body)).kind is WireMessageKind.command
            ]
            assert emitted == [pending_stop]
            for command in emitted:
                assert (await registry.run(command)).disposition is CommandDisposition.succeeded
            assert native.starts == native.prompts == 0
            assert native.answers == []
        finally:
            release.set()
            await asyncio.gather(replaying, return_exceptions=True)
            await runs.close()
            await link.wait_closed()

    asyncio.run(run())


@pytest.mark.parametrize("path", ["late_registration", "input_backpressure", "answer_backpressure"])
def test_closed_assignment_fences_late_registration_and_final_writer_admission(
    tmp_db: Connection,
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    """The authoritative close can commit before its event-loop sweep runs."""
    async def run() -> None:
        registry, _spool, native, start = native_registry
        identity = replace(IDENTITY, executor_incarnation=registry.incarnation)
        remote = endpoint(tmp_db, identity=identity)
        runs = remote.runs
        assert runs.link is not None
        link = runs.link
        stream = cast(MessageStream, link._stream)
        t = ticket(tmp_db)
        runs.store.register_child(start.child, "ticket", t.id, 3)
        if path == "late_registration":
            runs.store.close_child(start.child, 4)
            result = await asyncio.wait_for(runs.send_command(start), 1)
        else:
            command = start if path == "input_backpressure" else ChildCommand(
                "answer", start.child, ChildOperation.answer_permission_ask,
                TurnToken(start.child.conversation_id, 1),
                AnswerPermission(ask_id="q", option_id="yes"),
            )
            pending = _PendingCommand(command, asyncio.get_running_loop().create_future())
            runs.pending_commands[command.command_id] = pending
            entered = asyncio.Event()
            original_tell = link.tell

            async def tell(*args: Any, **kwargs: Any) -> bool:
                entered.set()
                return await original_tell(*args, **kwargs)

            class Conversations:
                async def emit_answer(
                    self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, None]]
                ) -> None:
                    await emit()

            runs.conversations = cast(Any, Conversations())
            runs.children[start.child.child_id] = cast(MacRemoteChild, SimpleNamespace(
                reference=start.child, saved_answers={}, can_emit_answer=lambda command: True,
            ))
            monkeypatch.setattr(link, "tell", tell)
            await link._lock.acquire()
            emitting = asyncio.create_task(runs._emit_command(pending, identity, link))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                # Direct closure deliberately leaves the one-time sweep pending.
                runs.store.close_child(start.child, 4)
                assert not pending.future.done()
            finally:
                link._lock.release()
            assert not await asyncio.wait_for(emitting, 1)
            result = pending.future.result()
        assert result.disposition is CommandDisposition.uncertain
        assert not stream.sent
        assert native.starts == native.prompts == 0
        assert native.answers == []
        await runs.close()
        await link.wait_closed()

    asyncio.run(run())
