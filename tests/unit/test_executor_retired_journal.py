"""Exact-child stop fences delayed journal writes without granting live authority."""

from __future__ import annotations

import asyncio
from sqlite3 import Connection
from types import SimpleNamespace
from typing import cast

import pytest
from tests.unit.test_executor_native_runtime import ScriptedChild
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.conversation.model_resolution.contracts import (
    SelectionKind,
)
from planner.core.clock import Clock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    ChildCommand,
    ChildOperation,
    ControlMessageWriter,
    EnvelopeKind,
    ExecutorIdentity,
    ReceiptDisposition,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.mac import as_object
from planner.executor.runtime import NativeChildRegistry
from planner.executor.vps import ExecutorEndpoint
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
        selection_kind=SelectionKind.concrete,
    )


def endpoint(conn: Connection, *, identity: ExecutorIdentity = IDENTITY) -> ExecutorEndpoint:
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    result = ExecutorEndpoint(path, cast(Clock, SimpleNamespace()))
    result.identity = identity
    result.writer = cast(ControlMessageWriter, Writer())
    result.ready = True
    result.store.begin_connection(identity, 1)
    result.store.mark_ready(identity, 2)
    return result


@pytest.mark.parametrize("bad_case", range(4))
def test_retired_delayed_start_refuses_without_live_child_authority(
    tmp_db: Connection,
    native_registry: tuple[NativeChildRegistry, FilesystemSpool, ScriptedChild, ChildCommand],
    bad_case: int,
) -> None:
    async def run() -> None:
        from dataclasses import replace

        from planner.conversation.backends.contracts import TurnToken
        from planner.executor.contracts import (
            BackendEvent,
            BackendEventMethod,
            CliWrite,
            CommandDisposition,
        )

        t = ticket(tmp_db)
        registry, spool, native, start = native_registry
        identity = replace(IDENTITY, executor_incarnation=registry.incarnation)
        remote = endpoint(tmp_db, identity=identity)
        remote.store.register_child(start.child, "ticket", t.id, 3)
        stop = ChildCommand("stop", start.child, ChildOperation.stop, None, {})
        assert (await registry.execute_command(stop)).disposition is CommandDisposition.succeeded
        (stopped,) = spool.pending()
        spool.acknowledge(await remote.accept_envelope(stopped, identity))
        remote.store.close_child(start.child, 4)
        outcome = await registry.execute_command(start)
        assert outcome.disposition is CommandDisposition.refused
        assert native.starts == 0
        assert registry.spool.pending() == ()
        assert registry.ask((start.command_id,))[0].outcome == outcome
        assert await registry.execute_command(start) == outcome
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
        row = registry.spool.mint_envelope(
            conversation_id=start.child.conversation_id, kind=kind, payload=as_object(payload)
        )
        receipt = await remote.accept_envelope(row, identity)
        assert receipt.disposition is ReceiptDisposition.refused
        assert remote.store.child_assignment(start.child) is None

    asyncio.run(run())
