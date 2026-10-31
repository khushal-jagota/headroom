"""Native submissions share the memory-only held line and retain honest outcomes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from sqlite3 import Connection
from typing import Any, cast

import pytest
from tests.support.executor_catalog import ScriptedCatalogObserver
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
from planner.conversation.model_resolution.contracts import (
    CatalogWorkspaceBinding,
    LaunchContext,
    SelectionKind,
)
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
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
from planner.executor.store import ExecutorStore
from planner.executor.vps_agent_writes import deliver_native_message
from planner.executor.vps_inbox import InboxStore
from planner.tickets import data
from planner.tickets.contracts import TicketLocale
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


class HeldCatalogObserver(ScriptedCatalogObserver):
    """Synthetic local context for the existing fake transport boundary."""

    def __init__(self) -> None:
        super().__init__(lambda: IDENTITY)
        self.model = "scripted"
        self.contexts: list[LaunchContext] = []

    async def capture_context(
        self, backend_key: ConversationBackendKey, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext:
        current = replace(
            await super().capture_context(backend_key, workspace=workspace),
            destination=TicketLocale.vps,
            executor_identity=None,
        )
        self.contexts.append(current)
        return current

    def verify_context(self, expected: LaunchContext) -> bool:
        return expected in self.contexts


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
    resolutions: int = 0

    async def new_core(self) -> SqliteProcessConversationSystem:
        for previous in self.systems:
            await previous.shutdown()

        async def native(command: ChildCommand) -> CommandOutcome:
            outcome = await self.native(command)
            conn = connect(self.path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                ExecutorStore.record_command_outcome_in_transaction(conn, outcome)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
            return outcome

        factory = MacRemoteChildFactory(
            lambda start, sink: ChildReference(
                "11111111-1111-1111-1111-111111111111",
                "child_" + start.conversation_id,
                start.conversation_id,
            ),
            native,
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
        observer = HeldCatalogObserver()
        system.configure_destination_validation(observer)
        from types import SimpleNamespace

        system._catalog_endpoint = cast(
            Any,
            SimpleNamespace(
                identity=IDENTITY,
                ready=True,
                owns_conversation=lambda _: True,
            ),
        )
        self.systems.append(system)
        return system

    def entry(self, submission_id: str, text: str) -> tuple[QueuedEnvelope, CliWrite]:
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
        return envelope, write

    async def deliver(
        self,
        envelope: QueuedEnvelope,
        write: CliWrite,
        *,
        system: SqliteProcessConversationSystem | None = None,
    ) -> AcceptanceReceipt:
        def resolve(connection: Connection, request: CliWrite, principal: Principal) -> Principal:
            self.resolutions += 1
            return CHIEF

        return await deliver_native_message(
            store=self.store,
            envelope=envelope,
            write=write,
            principal=self.source,
            identity=IDENTITY,
            clock=RealClock(),
            conversations=system or self.system,
            resolve_recipient=resolve,
        )

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
        selection_kind=SelectionKind.concrete,
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
    stored = fixture.store.lookup_receipt(envelope.sender_id, envelope.submission_id)
    return stored.result if stored is not None else {}


def test_waiting_native_messages_are_interrupted_without_restore_or_replay(
    tmp_db: Connection, tmp_path: Path
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            entries = [fixture.entry("first", "First Ω"), fixture.entry("second", "Second")]
            receipts = [await fixture.deliver(envelope, write) for envelope, write in entries]
            assert [receipt.result["fate"] for receipt in receipts] == ["queued", "queued"]
            assert [
                held.sender_message_id for held in await fixture.system.held_prompts("c_target")
            ] == ["first", "second"]
            for (envelope, write), receipt in zip(entries, receipts, strict=True):
                assert (await fixture.deliver(envelope, write)).result == replay_result(
                    fixture, envelope
                )
                fixture.spool.acknowledge(receipt)
            assert fixture.spool.pending() == ()
            assert fixture.resolutions == 2
            assert not await fixture.source_reply_credited()
            before = list(fixture.native.commands)
            fresh = await fixture.new_core()
            assert await fresh.held_prompts("c_target") == ()
            for (envelope, write), _receipt in zip(entries, receipts, strict=True):
                assert (
                    await fixture.deliver(envelope, write, system=fresh)
                ).result == replay_result(fixture, envelope)
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
            envelope, write = fixture.entry("lost_receipt", "Waiting before receipt")
            from planner.message_delivery import service

            send = service.send_message

            async def crash(*args: Any, **kwargs: Any) -> Any:
                await send(*args, **kwargs)
                raise InjectedCrash("first receipt interrupted")

            monkeypatch.setattr(service, "send_message", crash)
            with pytest.raises(InjectedCrash, match="first receipt interrupted"):
                await fixture.deliver(envelope, write)
            saved = fixture.store.lookup_receipt(envelope.sender_id, envelope.submission_id)
            assert saved is not None and saved.result["fate"] == "uncertain"
            before = list(fixture.native.commands)
            fresh = await fixture.new_core()
            monkeypatch.setattr(service, "send_message", send)
            receipt = await fixture.deliver(envelope, write, system=fresh)
            assert receipt.result["fate"] == "uncertain"
            assert (await fixture.deliver(envelope, write, system=fresh)).result == replay_result(
                fixture, envelope
            )
            assert fixture.resolutions == 1
            assert await fresh.held_prompts("c_target") == ()
            assert fixture.native.commands == before
        finally:
            await fixture.close()

    asyncio.run(run())
