"""Native submissions share the memory-only held line and retain honest outcomes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from sqlite3 import Connection
from typing import Any, cast

import pytest
from tests.support.executor_catalog import ScriptedCatalogObserver
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    HeldPromptPromotionMode,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
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
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AcceptanceReceipt,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CliWrite,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    ExecutorIdentity,
    JsonObject,
    OutboxEntry,
    QueuedEnvelope,
)
from planner.executor.mac_outbox import MacOutbox
from planner.executor.native_messages import deliver_native_message
from planner.executor.remote import RemoteBackendChild
from planner.executor.store import ExecutorStore
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
        self.fail_next_write = False
        self.uncertain_next_write = False
        self.uncertain_next_steer = False
        self.refuse_next_steer = False
        self.cancel_next_steer = False

    async def __call__(self, command: ChildCommand) -> CommandOutcome:
        self.commands.append(command)
        if command.operation is ChildOperation.write_prompt:
            if self.uncertain_next_write:
                self.uncertain_next_write = False
                return CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.uncertain,
                    {"reason": "write response lost", "composed_content_delivered": None},
                )
            if self.fail_next_write:
                self.fail_next_write = False
                raise InjectedCrash("native write outcome interrupted")
            result: JsonObject = {"kind": "prompt_accepted", "composed_content_delivered": True}
        elif command.operation is ChildOperation.steer:
            if self.uncertain_next_steer:
                self.uncertain_next_steer = False
                return CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.uncertain,
                    {"kind": "steer_uncertain", "composed_content_delivered": True},
                )
            if self.cancel_next_steer:
                self.cancel_next_steer = False
                raise asyncio.CancelledError
            if self.refuse_next_steer:
                self.refuse_next_steer = False
                result = {
                    "kind": "steer_refused",
                    "refusal_reason": "command_cannot_join_running_turn",
                }
            else:
                result = {"kind": "steer_accepted", "composed_content_delivered": True}
        else:
            result = {}
        return CommandOutcome(
            command.command_id, command.child, CommandDisposition.succeeded, result
        )

    def writes(self) -> list[ChildCommand]:
        return [
            command for command in self.commands if command.operation is ChildOperation.write_prompt
        ]


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
    store: ExecutorStore
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

        def factory(**kwargs: Any) -> RemoteBackendChild:
            start = kwargs["resolved_start"]
            return RemoteBackendChild(
                ChildReference(
                    "11111111-1111-1111-1111-111111111111",
                    "child_" + start.conversation_id,
                    start.conversation_id,
                ),
                native,
                lambda start: canonical_snapshot(),
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
        metadata = await (system or self.system)._store.read_executor_turn("c_source")
        assert metadata is not None
        return {"kind": "chief", "id": "chief"} in json.loads(metadata[2])[
            "explicit_reply_recipients"
        ]

    async def close(self) -> None:
        self.spool.close()
        for system in self.systems:
            for state in system._conversations.values():
                state.child = None
            await system.shutdown()


async def boot(connection: Connection, root: Path) -> HeldFixture:
    path = str(connection.execute("PRAGMA database_list").fetchone()[2])
    store = ExecutorStore(path)
    store.begin_connection(IDENTITY, 1000)
    store.mark_ready(IDENTITY, 1001)
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


def operation_result(fixture: HeldFixture, submission_id: str) -> dict[str, Any]:
    row = fixture.connection.execute(
        "SELECT result_json FROM executor_native_messages WHERE submission_id=?",
        (submission_id,),
    ).fetchone()
    return json.loads(row[0])  # type: ignore[no-any-return]


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
                assert operation_result(fixture, envelope.submission_id) == {
                    "target": {"kind": "chief", "id": "chief"},
                    "conversation_id": "c_target",
                    "fate": "interrupted",
                }
                assert (
                    await fixture.deliver(envelope, write, system=fresh)
                ).result == replay_result(fixture, envelope)
            assert fixture.native.commands == before
            assert not await fixture.source_reply_credited(fresh)
        finally:
            await fixture.close()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["discard", "promote", "drain", "uncertain_steer"])
def test_native_terminal_held_outcome_retains_receipt_and_composed_reply_credit(
    tmp_db: Connection, tmp_path: Path, action: str
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("terminal", "Exact reply Ω")
            await fixture.deliver(envelope, write)
            held = (await fixture.system.held_prompts("c_target"))[0]
            if action == "discard":
                assert await fixture.system.discard_held_prompt("c_target", held.held_prompt_id)
            elif action == "uncertain_steer":
                fixture.native.uncertain_next_steer = True
                assert isinstance(
                    await fixture.system.promote_held_prompt(
                        "c_target", held.held_prompt_id, HeldPromptPromotionMode.steer
                    ),
                    PromptDeliveryUncertain,
                )
            elif action == "promote":
                assert isinstance(
                    await fixture.system.promote_held_prompt(
                        "c_target", held.held_prompt_id, HeldPromptPromotionMode.send_now
                    ),
                    PromptDeliveryStarted,
                )
            else:
                await fixture.system.interrupt("c_target")
                await fixture.system.wait_until_quiescent()
            assert await fixture.system.held_prompts("c_target") == ()
            assert operation_result(fixture, "terminal")["fate"] == (
                "discarded"
                if action == "discard"
                else "uncertain"
                if action == "uncertain_steer"
                else "started"
            )
            assert await fixture.source_reply_credited() is (action != "discard")
            before = list(fixture.native.commands)
            fresh = await fixture.new_core()
            assert (await fixture.deliver(envelope, write, system=fresh)).result == replay_result(
                fixture, envelope
            )
            assert await fresh.held_prompts("c_target") == ()
            assert fixture.native.commands == before
        finally:
            await fixture.close()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["promote", "drain", "cancel_steer"])
def test_unknown_native_dispatch_quarantines_live_line_and_is_not_retried_after_restart(
    tmp_db: Connection, tmp_path: Path, action: str
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("uncertain", "Uncertain exact Ω")
            await fixture.deliver(envelope, write)
            held = (await fixture.system.held_prompts("c_target"))[0]
            if action == "cancel_steer":
                fixture.native.cancel_next_steer = True
                with pytest.raises(asyncio.CancelledError):
                    await fixture.system.promote_held_prompt(
                        "c_target", held.held_prompt_id, HeldPromptPromotionMode.steer
                    )
            elif action == "promote":
                fixture.native.fail_next_write = True
                with pytest.raises(InjectedCrash):
                    await fixture.system.promote_held_prompt(
                        "c_target", held.held_prompt_id, HeldPromptPromotionMode.send_now
                    )
            else:
                fixture.native.uncertain_next_write = True
                await fixture.system.interrupt("c_target")
                await fixture.system.wait_until_quiescent()
            pending = await fixture.system.held_prompts("c_target")
            assert len(pending) == 1 and pending[0].delivery_uncertain
            assert pending[0].content == held.content
            assert operation_result(fixture, "uncertain")["fate"] == "uncertain"
            later_envelope, later_write = fixture.entry("later", "Wait behind unknown")
            assert (await fixture.deliver(later_envelope, later_write)).result["fate"] == "queued"
            later = (await fixture.system.held_prompts("c_target"))[1]
            before = list(fixture.native.commands)
            assert isinstance(
                await fixture.system.promote_held_prompt(
                    "c_target", later.held_prompt_id, HeldPromptPromotionMode.send_now
                ),
                PromptDeliveryQueued,
            )
            await fixture.system.kill("c_target")
            live = await fixture.system.held_prompts("c_target")
            assert len(live) == 1 and live[0].delivery_uncertain
            assert operation_result(fixture, "later")["fate"] == "discarded"
            assert not await fixture.source_reply_credited()
            fresh = await fixture.new_core()
            assert await fresh.held_prompts("c_target") == ()
            assert operation_result(fixture, "uncertain")["fate"] == "uncertain"
            assert (await fixture.deliver(envelope, write, system=fresh)).result == replay_result(
                fixture, envelope
            )
            assert fixture.native.writes() == [
                command for command in before if command.operation is ChildOperation.write_prompt
            ]
        finally:
            await fixture.close()

    asyncio.run(run())


def test_known_safe_steer_requeue_keeps_live_order_but_loses_waiting_text_on_restart(
    tmp_db: Connection, tmp_path: Path
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            first, first_write = fixture.entry("command", "/scripted Ω")
            await fixture.deliver(first, first_write)
            second, second_write = fixture.entry("other", "Other held message")
            await fixture.deliver(second, second_write)
            initial = await fixture.system.held_prompts("c_target")
            fixture.native.refuse_next_steer = True
            fate = await fixture.system.promote_held_prompt(
                "c_target", initial[0].held_prompt_id, HeldPromptPromotionMode.steer
            )
            assert isinstance(fate, PromptDeliveryQueued)
            queued = await fixture.system.held_prompts("c_target")
            assert [held.sender_message_id for held in queued] == ["other", "command"]
            assert queued[1].content == initial[0].content
            assert queued[1].held_prompt_id != initial[0].held_prompt_id
            assert not any(held.delivery_uncertain for held in queued)
            assert not await fixture.source_reply_credited()
            before = list(fixture.native.commands)
            fresh = await fixture.new_core()
            assert await fresh.held_prompts("c_target") == ()
            assert operation_result(fixture, "command")["fate"] == "interrupted"
            assert operation_result(fixture, "other")["fate"] == "interrupted"
            assert (
                await fixture.deliver(first, first_write, system=fresh)
            ).result == replay_result(fixture, first)
            assert fixture.native.commands == before
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
            accept = fixture.store.accept

            def crash(*args: Any, **kwargs: Any) -> AcceptanceReceipt:
                raise InjectedCrash("first receipt interrupted")

            monkeypatch.setattr(fixture.store, "accept", crash)
            with pytest.raises(InjectedCrash, match="first receipt interrupted"):
                await fixture.deliver(envelope, write)
            assert fixture.store.lookup_receipt(envelope.sender_id, envelope.submission_id) is None
            assert operation_result(fixture, "lost_receipt")["fate"] == "queued"
            before = list(fixture.native.commands)
            fresh = await fixture.new_core()
            monkeypatch.setattr(fixture.store, "accept", accept)
            receipt = await fixture.deliver(envelope, write, system=fresh)
            assert receipt.result["fate"] == "interrupted"
            assert (await fixture.deliver(envelope, write, system=fresh)).result == replay_result(
                fixture, envelope
            )
            assert fixture.resolutions == 1
            assert await fresh.held_prompts("c_target") == ()
            assert fixture.native.commands == before
        finally:
            await fixture.close()

    asyncio.run(run())
