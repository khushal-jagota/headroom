"""Retained source-retirement receipts fence delayed journal writes after switching removal."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import cast

import pytest
from tests.unit.test_executor_source_retirement import IDENTITY, build

from planner.conversation.contracts import (
    MoveRetirementDisposition,
)
from planner.conversation.model_resolution.contracts import (
    SelectionKind,
)
from planner.core.clock import Clock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.errors import PlannerError
from planner.executor.contracts import (
    EnvelopeKind,
    ExecutorIdentity,
)
from planner.executor.mac import as_object
from planner.executor.vps import ExecutorEndpoint
from planner.tickets import data
from planner.tickets.contracts import Ticket


class Writer:
    closing = False

    def is_closing(self) -> bool:
        return self.closing


def ticket(conn: Connection) -> Ticket:
    return data.create_ticket(
        conn,
        title="Move lifecycle",
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
    result.writer = cast(asyncio.StreamWriter, Writer())
    result.ready = True
    result.store.begin_connection(identity, 1)
    result.store.mark_ready(identity, 2)
    return result




















@pytest.mark.parametrize("bad_case", range(4))
def test_retired_delayed_start_refusal_journal_is_accepted_with_no_live_child_authority(
    tmp_db: Connection, tmp_path: Path, bad_case: int
) -> None:
    async def run() -> None:
        from dataclasses import replace

        from planner.conversation.backends.contracts import TurnToken
        from planner.executor.contracts import (
            BackendEvent,
            BackendEventMethod,
            CliWrite,
            CommandDisposition,
            SourceRetirementRequest,
        )

        t = ticket(tmp_db)
        remote = endpoint(tmp_db)
        registry, native, start = build(tmp_path)
        remote.store.register_child(start.child, "ticket", t.id, 3)
        result = await registry.retire_source(
            "move", SourceRetirementRequest(IDENTITY, t.id, None, (start.child,)), IDENTITY
        )
        assert result.result.disposition is MoveRetirementDisposition.retired
        remote.store.close_child(start.child, 4)
        outcome = await registry.execute_command(start)
        assert outcome.disposition is CommandDisposition.refused
        assert native.starts == native.stops == 0
        (envelope,) = registry.spool.pending()
        receipt = await remote.accept_envelope(envelope, IDENTITY)
        registry.spool.acknowledge(receipt)
        assert registry.spool.pending() == ()
        reconciliation = remote.store.reconcile(registry.inventory(IDENTITY), 5)
        assert reconciliation.acknowledged_command_ids == (start.command_id,)
        for command_id in reconciliation.acknowledged_command_ids:
            registry.confirm_command(command_id)
        assert registry.inventory(IDENTITY).retained_command_outcomes == ()
        # A closed identity only settles a conclusive no-effect refusal. It grants
        # neither a native event, CLI write, uncertain outcome nor applied answer.
        bad_payloads = [
            (
                EnvelopeKind.command_outcome,
                replace(outcome, command_id="unknown", disposition=CommandDisposition.uncertain),
            ),
            (
                EnvelopeKind.command_outcome,
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
        with pytest.raises(PlannerError, match="no VPS assignment"):
            await remote.accept_envelope(row, IDENTITY)

    asyncio.run(run())
