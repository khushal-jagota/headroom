"""Offline inventory-to-replay proof using real encoded, paired transports."""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest
from tests.support.executor_control import MessageReader

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.db import connect
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    ChildCommand,
    ChildOperation,
    CommandOutcome,
    ExecutorIdentity,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import MacExecutor
from planner.executor.outbox_storage import DATABASE_NAME, connect_database, initialize_database
from planner.executor.vps import ExecutorEndpoint
from planner.executor.wire import encode_message


def isolate_replay_paths(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not let copied deployment metadata select any writable deployment path."""
    for name in tuple(os.environ):
        if name.startswith("PLAN_") or name == "PANELS_DIAGNOSTIC_CAPTURE_CONFIG":
            monkeypatch.delenv(name)
    for name, relative in {
        "HOME": "home",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "share",
        "CODEX_HOME": "codex",
        "CLAUDE_CONFIG_DIR": "claude",
        "PLAN_HERMES_HOME": "hermes",
        "PLAN_DB_PATH": "vps.sqlite3",
        "PLAN_LOGS_DIR": "logs",
        "PLAN_DISPATCHER_LOCK_PATH": "run/dispatcher.lock",
        "PLAN_SERVER_CONTROL_SOCKET": "run/control.sock",
        "PLAN_CONFIG_PATH": "absent.yaml",
        "PLAN_EXECUTOR_ROOT": "mac",
        "PLAN_NATIVE_SKILLS_DIR": "skills",
        "PLAN_TICKET_FILES_DIR": "files/tickets",
    }.items():
        monkeypatch.setenv(name, str(root / relative))
    monkeypatch.chdir(root)


class PairedWriter:
    """A typed writer delivering one binary message at a time, without a listener."""

    def __init__(self, reader: MessageReader) -> None:
        self.reader = reader
        self.closed = False
        self.frames: list[bytes] = []

    async def send(self, message: WireMessage) -> None:
        data = encode_message(message)
        assert not self.closed
        self.frames.append(data)
        self.reader.feed_data(data)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.reader.feed_eof()

    async def wait_closed(self) -> None:
        pass

    def is_closing(self) -> bool:
        return self.closed


def fail_backend(**arguments: Any) -> NoReturn:
    raise AssertionError("offline proof attempted backend execution")


def stored_rows(db_path: str, table: str) -> tuple[tuple[Any, ...], ...]:
    connection = connect(db_path)
    try:
        return tuple(
            tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1,2")
        )
    finally:
        connection.close()


async def _prove_historical_replay(
    root: Path,
    db_path: str,
    identity: ExecutorIdentity,
    outcome: CommandOutcome,
    command: ChildCommand,
    envelope: QueuedEnvelope,
    cleanup: contextlib.AsyncExitStack,
) -> dict[str, Any]:
    """Reconcile retained evidence, replay once, then replay the identical journal member."""
    endpoint = ExecutorEndpoint(db_path, RealClock())
    assert endpoint.store.has_retained_command(outcome)
    assert endpoint.store.has_accepted_command_outcome(outcome)
    assert outcome.child.executor_incarnation != identity.executor_incarnation
    endpoint.store.begin_connection(identity, 2000)
    endpoint.identity = identity
    baseline = {
        table: stored_rows(db_path, table)
        for table in (
            "executor_command_outcomes",
            "executor_receipts",
            "executor_child_assignments",
            "executor_core_turns",
            "executor_asks",
        )
    }
    from planner.conversation.runtime_composition import (
        SignalRuntimeComposition,
        bind_admission,
        transaction_control,
    )

    composition = SignalRuntimeComposition(db_path, 5000)
    cleanup.callback(composition.close)
    composition.bootstrap()
    system = SqliteProcessConversationSystem(
        store=ConversationStore(
            db_path,
            signal_coordinator=composition.coordinator,
            bind_signal_admission=bind_admission,
            runtime_reconciliation_admission=composition.initialization,
            transaction_control_guard=transaction_control,
        ),
        backend_child_factories={key: fail_backend for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(db_path),
        runtime_ownership=composition.ownership,
    )
    composition.transfer_ownership()
    cleanup.push_async_callback(system.shutdown)
    composition.initialize()
    attachment = composition.coordinator.attach_signal_publication(
        install=lambda conn, snapshot: None,
        participate=lambda conn, batch: None,
        unavailable=lambda reason: None,
    )
    cleanup.callback(attachment.close)
    composition.coordinator.admit_runtime()
    authority_baseline = {
        table: stored_rows(db_path, table)
        for table in (
            "executor_child_assignments",
            "executor_core_turns",
            "executor_asks",
        )
    }
    endpoint.runtime = SimpleNamespace(
        system=system, message_files=ConversationMessageFiles(db_path)
    )
    paths = ExecutorPaths(root / "mac")
    (paths.spool / "entries").mkdir(parents=True)
    spool_db = connect_database(paths.spool / DATABASE_NAME)
    initialize_database(spool_db, envelope.sender_id)
    spool_db.close()
    mac = MacExecutor(
        paths=paths,
        revision=identity.application_revision,
        executor_id="offline",
        panels_server_url="https://localhost",
    )
    cleanup.callback(mac.spool.close)
    mac.identity = identity
    mac.registry.incarnation = identity.executor_incarnation
    mac.registry.factory = lambda *args: fail_backend()
    mac.registry.outcomes[outcome.command_id] = outcome
    mac.registry._commands[command.command_id] = command
    mac.registry._outcomes_published.add(outcome.command_id)
    mac.spool.enqueue(envelope)
    assert not mac.registry.children
    assert all(state.child is None for state in system._conversations.values())
    mac._handshake_accepted = True
    mac_reader, vps_reader = MessageReader(), MessageReader()
    vps_writer, mac_writer = PairedWriter(mac_reader), PairedWriter(vps_reader)
    endpoint.writer, mac._writer = vps_writer, mac_writer
    received: list[WireMessage] = []

    async def serve() -> None:
        while True:
            message = await vps_reader.receive()
            received.append(message)
            await endpoint._handle_message(message, identity, vps_writer)

    tasks = [asyncio.create_task(serve()), asyncio.create_task(mac._read_loop(mac_reader))]
    pending = []
    try:
        for operation in (ChildOperation.start, ChildOperation.write_prompt, ChildOperation.stop):
            future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
            pending.append(future)
            item = replace(command, operation=operation)
            endpoint.pending_commands[command.command_id] = (item, future)
            mac.registry._confirmed_command_ids.discard(command.command_id)
            await asyncio.wait_for(mac._reconcile(), 5)
            assert not future.done()
            assert command.command_id in mac.registry._confirmed_command_ids
        assert all(not future.done() for future in pending)
        assert not mac.registry.children
        assert all(state.child is None for state in system._conversations.values())
        assert (
            stored_rows(db_path, "executor_child_assignments")
            == authority_baseline["executor_child_assignments"]
        )
        assert (
            stored_rows(db_path, "executor_core_turns") == authority_baseline["executor_core_turns"]
        )
        # Canonical reconciliation updates observation times/generations, but cannot
        # restore a historical callback to reachable.
        connection = connect(db_path)
        try:
            assert (
                connection.execute(
                    "SELECT count(*) FROM executor_asks WHERE executor_incarnation=? "
                    "AND reachability='reachable'",
                    (outcome.child.executor_incarnation,),
                ).fetchone()[0]
                == 0
            )
        finally:
            connection.close()
        assert endpoint.store.lookup_receipt(envelope.sender_id, envelope.submission_id) is None
        # Inventory has finished; set readiness without the ready handler's dispatch/resume loops.
        endpoint.store.mark_ready(identity, 2001)
        endpoint.ready = True

        def publications() -> int:
            connection = connect(db_path)
            try:
                return int(
                    connection.execute(
                        "SELECT count(*) FROM conversation_events "
                        "WHERE conversation_id=? AND kind=?",
                        (envelope.conversation_id, "tool_call_started"),
                    ).fetchone()[0]
                )
            finally:
                connection.close()

        count = publications()
        await asyncio.wait_for(mac._replay(), 5)
        first = endpoint.store.lookup_receipt(envelope.sender_id, envelope.submission_id)
        assert first is not None and first.disposition is ReceiptDisposition.accepted
        assert first.result.get("applied") is True
        assert not mac.spool.pending()
        assert publications() == count + 1
        # A fresh spool recreates lost local receipt knowledge while retaining exact bytes.
        mac.spool.close()
        from planner.executor.journal import FilesystemSpool

        duplicate_root = root / "duplicate-spool"
        (duplicate_root / "entries").mkdir(parents=True)
        duplicate_db = connect_database(duplicate_root / DATABASE_NAME)
        initialize_database(duplicate_db, envelope.sender_id)
        duplicate_db.close()
        mac.spool = FilesystemSpool(duplicate_root)
        cleanup.callback(mac.spool.close)
        mac.spool.enqueue(envelope)
        await asyncio.wait_for(mac._replay(), 5)
        assert endpoint.store.lookup_receipt(envelope.sender_id, envelope.submission_id) == first
        assert not mac.spool.pending() and publications() == count + 1
        assert (
            stored_rows(db_path, "executor_command_outcomes")
            == baseline["executor_command_outcomes"]
        )
        after_receipts = stored_rows(db_path, "executor_receipts")
        assert all(row in after_receipts for row in baseline["executor_receipts"])
        assert len(after_receipts) == len(baseline["executor_receipts"]) + 1
        assert all(not future.done() for future in pending)
        assert not mac.registry.children
        return {
            "sequence": envelope.sequence,
            "receipt_disposition": first.disposition.value,
            "historical_pending_futures_remain_pending": True,
            "original_outcomes_and_receipts_unchanged": True,
            "historical_authority_unchanged": True,
            "new_publications": 1,
            "duplicate_receipt_identical": True,
            "spool_acknowledged": True,
            "inventory_requests": sum(
                m.kind is WireMessageKind.reconcile_inventory for m in received
            ),
            "backend_execution": False,
            "listeners_started": False,
        }
    finally:
        for task in tasks:
            task.cancel()
        failures = await asyncio.gather(*tasks, return_exceptions=True)
        endpoint.pending_commands.clear()
        mac_writer.close()
        vps_writer.close()
        mac.spool.close()
        await system.shutdown()
        for failure in failures:
            if isinstance(failure, Exception):
                raise failure


async def prove_historical_replay(
    root: Path,
    db_path: str,
    identity: ExecutorIdentity,
    outcome: CommandOutcome,
    command: ChildCommand,
    envelope: QueuedEnvelope,
) -> dict[str, Any]:
    async with contextlib.AsyncExitStack() as cleanup:
        return await _prove_historical_replay(
            root,
            db_path,
            identity,
            outcome,
            command,
            envelope,
            cleanup,
        )
