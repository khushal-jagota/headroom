"""Exact-child stop fences delayed journal writes without granting live authority."""

from __future__ import annotations

import asyncio
from sqlite3 import Connection
from types import SimpleNamespace
from typing import cast

import pytest
from tests.support.executor_control import message_link
from tests.unit.test_executor_native_runtime import ScriptedChild
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.core.clock import Clock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    EnvelopeKind,
    OutboxEntry,
    ReceiptDisposition,
    as_object,
    decode_command_arguments,
)
from planner.executor.vps_transport import VpsExecutor
from planner.tickets import data
from planner.tickets.contracts import Ticket

IDENTITY = ExecutorIdentity(
    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "ca27d941-1e77-4ac9-a102-e0ac14b51626", 1
)


class Writer:
    closing = False

    def is_closing(self) -> bool:
        return self.closing


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
    result.runs.link = message_link(Writer(), identity)
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
        assert registry.ask((start.command_id,))[0].outcome == outcome
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
        row = registry.spool.add(
            OutboxEntry(
                conversation_id=start.child.conversation_id, kind=kind, payload=as_object(payload)
            )
        )
        receipt = await remote.inbox.accept(row, identity)
        assert receipt.disposition is ReceiptDisposition.refused
        assert remote.runs.store.child_assignment(start.child) is None

    asyncio.run(run())
