"""Native submissions share the memory-only held line and retain honest outcomes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection
from typing import Any, cast

import pytest
from tests.support.executor_inbox import lookup_receipt
from tests.support.executor_turns import MacRemoteChildFactory
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.mac_outbox import MacOutbox
from planner.executor.protocol import (
    AcceptanceReceipt,
    ChildCommand,
    ChildOperation,
    CliWrite,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    JsonObject,
    OutboxEntry,
    QueuedEnvelope,
    encode_value,
)
from planner.executor.vps_inbox import InboxStore
from planner.executor.vps_transport import VpsExecutor
from planner.tickets import data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY

CHIEF = Principal(PrincipalKind.chief, "chief")
IDENTITY = ExecutorIdentity(
    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "11111111-1111-1111-1111-111111111111", 1
)


class InjectedCrash(RuntimeError):
    pass


class ScriptedNative:
    """Native boundary only; the real core and SQLite own all admission semantics."""

    def __init__(self) -> None:
        self.commands: list[ChildCommand] = []

    async def __call__(self, command: ChildCommand) -> CommandOutcome:
        self.commands.append(command)
        return CommandOutcome(
            command.command_id,
            command.child,
            CommandDisposition.succeeded,
            {"kind": "prompt_accepted", "composed_content_delivered": True}
            if command.operation is ChildOperation.write_prompt
            else {},
        )


@dataclass
class HeldFixture:
    connection: Connection
    path: str
    store: InboxStore
    spool: MacOutbox
    native: ScriptedNative
    system: SqliteProcessConversationSystem = field(init=False)
    source: Principal
    systems: list[SqliteProcessConversationSystem]
    endpoint: VpsExecutor = field(init=False)

    async def new_core(self) -> SqliteProcessConversationSystem:
        for previous in self.systems:
            await previous.shutdown()

        factory = MacRemoteChildFactory(
            lambda start, sink: ChildReference(
                "11111111-1111-1111-1111-111111111111",
                "child_" + start.conversation_id,
                start.conversation_id,
            ),
            self.native,
            lambda start: canonical_snapshot(),
            db_path=self.path,
        )

        system = SqliteProcessConversationSystem(
            store=ConversationStore(self.path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(self.path),
            idle_child_stop_after_seconds=0,
            idle_child_sweep_interval_seconds=0.005,
        )
        from types import SimpleNamespace

        system.machine_catalog = cast(
            Any,
            SimpleNamespace(
                identity=IDENTITY,
                ready=True,
                owns_conversation=lambda _: True,
            ),
        )
        self.endpoint = VpsExecutor(self.path, RealClock())
        self.endpoint.bind_conversations(system)
        self.systems.append(system)
        return system

    def entry(self, submission_id: str, text: str) -> QueuedEnvelope:
        write = CliWrite(
            "message.send",
            {
                "body": {
                    "target": {"kind": "chief", "id": "chief"},
                    "message": text,
                    "mode": "queue",
                }
            },
            ChildReference("11111111-1111-1111-1111-111111111111", "child_c_source", "c_source"),
            TurnToken("c_source", 1),
        )
        payload = encode_value(write)
        assert isinstance(payload, dict)
        envelope = self.spool.add(
            OutboxEntry(
                conversation_id="c_source",
                kind=EnvelopeKind.cli_write,
                payload=payload,
                submission_id=submission_id,
            )
        )
        return envelope

    async def deliver(
        self,
        envelope: QueuedEnvelope,
        *,
        system: SqliteProcessConversationSystem | None = None,
    ) -> AcceptanceReceipt:
        self.endpoint.bind_conversations(system or self.system)
        return await self.endpoint.inbox.accept(envelope, IDENTITY)

    async def source_reply_credited(
        self, system: SqliteProcessConversationSystem | None = None
    ) -> bool:
        state = (system or self.system)._conversations["c_source"]
        token = state.running_turn.token if state.running_turn else state.last_ended_turn_token
        assert token is not None
        return (
            self.connection.execute(
                "SELECT 1 FROM conversation_reply_credits WHERE conversation_id='c_source' "
                "AND turn_number=? AND principal_kind='chief' AND principal_id='chief'",
                (token.turn_number,),
            ).fetchone()
            is not None
        )

    async def close(self) -> None:
        self.spool.close()
        for system in self.systems:
            for state in system._conversations.values():
                state.child = None
            await system.shutdown()


async def boot(connection: Connection, root: Path) -> HeldFixture:
    path = str(connection.execute("PRAGMA database_list").fetchone()[2])
    store = InboxStore(path)
    store.runs.begin_connection(IDENTITY, 1000)
    store.runs.mark_ready(IDENTITY, 1001)
    ticket = data.create_ticket(
        connection,
        title="Held source",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="scripted",
    )
    fixture = HeldFixture(
        connection,
        path,
        store,
        MacOutbox(root / "spool"),
        ScriptedNative(),
        Principal(PrincipalKind.ticket, ticket.id),
        [],
    )
    fixture.system = await fixture.new_core()
    system = fixture.system
    await system.start_conversation(
        ConversationStartRequest(
            "c_source",
            "scripted",
            role_materials=ConversationRoleMaterials(
                "Source",
                (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id)),
            ),
        )
    )
    data.write_ticket_conversation_start(
        connection,
        ticket.id,
        conversation_id="c_source",
        backend="codex",
        model="scripted",
        reasoning_effort=None,
        now=2,
    )
    await system.start_conversation(
        ConversationStartRequest(
            "c_target",
            "scripted",
            role_materials=ConversationRoleMaterials(
                "Chief",
                (("PLAN_ACTOR", "chief"),),
            ),
        )
    )
    connection.execute(
        "INSERT INTO agents(agent_key,conversation_id) VALUES (?,?) "
        "ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
        (CHIEF_SETTINGS_KEY, "c_target"),
    )
    await system.send(
        "c_source",
        text_message_content("Reply to Chief"),
        sender_label="Chief",
        sender=CHIEF,
        recipient=fixture.source,
    )
    await system.send(
        "c_target",
        text_message_content("Busy turn"),
        sender_label="owner",
        sender=OWNER_PRINCIPAL,
        recipient=CHIEF,
    )
    return fixture


def replay_result(fixture: HeldFixture, envelope: QueuedEnvelope) -> JsonObject:
    stored = lookup_receipt(fixture.store, envelope.sender_id, envelope.submission_id)
    return stored.result if stored is not None else {}


def test_waiting_native_messages_are_interrupted_without_restore_or_replay(
    tmp_db: Connection, tmp_path: Path
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            entries = [fixture.entry("first", "First Ω"), fixture.entry("second", "Second")]
            receipts = [await fixture.deliver(envelope) for envelope in entries]
            assert [receipt.result["fate"] for receipt in receipts] == ["queued", "queued"]
            assert [
                held.sender_message_id for held in await fixture.system.held_prompts("c_target")
            ] == ["first", "second"]
            for envelope, receipt in zip(entries, receipts, strict=True):
                assert (await fixture.deliver(envelope)).result == replay_result(fixture, envelope)
                fixture.spool.acknowledge(receipt)
            assert fixture.spool.pending() == ()
            assert not await fixture.source_reply_credited()
            before = list(fixture.native.commands)
            fresh = await fixture.new_core()
            assert await fresh.held_prompts("c_target") == ()
            for envelope, _receipt in zip(entries, receipts, strict=True):
                assert (await fixture.deliver(envelope, system=fresh)).result == replay_result(
                    fixture, envelope
                )
            assert fixture.native.commands == before
            assert not await fixture.source_reply_credited(fresh)
        finally:
            await fixture.close()

    asyncio.run(run())


def test_lost_first_receipt_keeps_operation_binding_and_never_repeats_held_send(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope = fixture.entry("lost_receipt", "Waiting before receipt")
            from planner.message_delivery import service

            send = service.send_message

            async def crash(*args: Any, **kwargs: Any) -> Any:
                await send(*args, **kwargs)
                raise InjectedCrash("first receipt interrupted")

            monkeypatch.setattr(service, "send_message", crash)
            with pytest.raises(InjectedCrash, match="first receipt interrupted"):
                await fixture.deliver(envelope)
            saved = lookup_receipt(fixture.store, envelope.sender_id, envelope.submission_id)
            assert saved is not None and saved.result["fate"] == "uncertain"
            before = list(fixture.native.commands)
            fresh = await fixture.new_core()
            monkeypatch.setattr(service, "send_message", send)
            receipt = await fixture.deliver(envelope, system=fresh)
            assert receipt.result["fate"] == "uncertain"
            assert (await fixture.deliver(envelope, system=fresh)).result == replay_result(
                fixture, envelope
            )
            assert await fresh.held_prompts("c_target") == ()
            assert fixture.native.commands == before
        finally:
            await fixture.close()

    asyncio.run(run())
