"""Canonical held admissions survive Mac acknowledgment and VPS process loss."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    AddressedPromptDeliveryReceipt,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    HeldPromptPromotionMode,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
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
    QueuedEnvelope,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.native_messages import deliver_native_message
from planner.executor.remote import RemoteBackendChild
from planner.executor.store import ExecutorStore
from planner.tickets import data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY

CHIEF = Principal(PrincipalKind.chief, "chief")
IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "process", 1)


class InjectedCrash(RuntimeError):
    pass


class ScriptedNative:
    """Native boundary only; the real core and SQLite own all admission semantics."""

    def __init__(self) -> None:
        self.commands: list[ChildCommand] = []
        self.fail_next_write = False
        self.uncertain_next_write = False
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


class BoundarySystem(SqliteProcessConversationSystem):
    crash_after_held_commit = False
    pause_drain = False

    async def send_with_receipt(
        self, conversation_id: str, content: MessageContent, **kwargs: Any
    ) -> AddressedPromptDeliveryReceipt:
        receipt = await super().send_with_receipt(conversation_id, content, **kwargs)
        if self.crash_after_held_commit and isinstance(receipt.fate, PromptDeliveryQueued):
            raise InjectedCrash("held bytes and first receipt committed before acknowledgment")
        return receipt

    async def _drain_held_prompts(self, state: Any) -> None:
        if not self.pause_drain:
            await super()._drain_held_prompts(state)


@dataclass
class HeldFixture:
    connection: Connection
    path: str
    store: ExecutorStore
    spool: FilesystemSpool
    native: ScriptedNative
    system: BoundarySystem = field(init=False)
    source: Principal
    systems: list[BoundarySystem]
    resolutions: int = 0

    def new_core(self) -> BoundarySystem:
        def factory(**kwargs: Any) -> RemoteBackendChild:
            start = kwargs["resolved_start"]
            return RemoteBackendChild(
                ChildReference("process", "child_" + start.conversation_id, start.conversation_id),
                self.native,
            )

        system = BoundarySystem(
            store=ConversationStore(self.path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(self.path),
            idle_child_stop_after_seconds=0,
            idle_child_sweep_interval_seconds=0.005,
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
            ChildReference("process", "child_c_source", "c_source"),
            TurnToken("c_source", 1),
        )
        payload = encode_value(write)
        assert isinstance(payload, dict)
        envelope = self.spool.mint_envelope(
            conversation_id="c_source",
            kind=EnvelopeKind.cli_write,
            payload=payload,
            submission_id=submission_id,
        )
        return envelope, write

    async def deliver(
        self,
        envelope: QueuedEnvelope,
        write: CliWrite,
        *,
        system: BoundarySystem | None = None,
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

    async def source_reply_credited(self, system: BoundarySystem | None = None) -> bool:
        metadata = await (system or self.system)._store.read_executor_turn("c_source")
        assert metadata is not None
        return {"kind": "chief", "id": "chief"} in json.loads(metadata[2])[
            "explicit_reply_recipients"
        ]

    def held_rows(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM executor_native_messages ORDER BY held_admission_order"
            )
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
    )
    fixture = HeldFixture(
        connection,
        path,
        store,
        FilesystemSpool(root / "spool"),
        ScriptedNative(),
        Principal(PrincipalKind.ticket, ticket.id),
        [],
    )
    fixture.system = fixture.new_core()
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


async def eventually(condition: Callable[[], bool], detail: str) -> None:
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(detail)


def test_held_commit_before_ack_replay_and_mac_ack_restore_exact_target_order(
    tmp_db: Connection,
    tmp_path: Path,
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            fixture.system.crash_after_held_commit = True
            entries = [
                fixture.entry("held_first", "First line\nUnicode Ω preserved"),
                fixture.entry("held_second", "Second line\nExact trailing space "),
            ]
            first_receipts: list[AcceptanceReceipt] = []
            for envelope, write in entries:
                with pytest.raises(InjectedCrash, match="before acknowledgment"):
                    await fixture.deliver(envelope, write)
                receipt = fixture.store.lookup_receipt(envelope.sender_id, envelope.submission_id)
                assert receipt is not None and receipt.result["fate"] == "queued"
                first_receipts.append(receipt)
            pending = await fixture.system.held_prompts("c_target")
            assert [item.sender_message_id for item in pending] == ["held_first", "held_second"]
            native_before = list(fixture.native.commands)
            for (envelope, write), receipt in zip(entries, first_receipts, strict=True):
                assert await fixture.deliver(envelope, write) == receipt
                assert await fixture.deliver(envelope, write) == receipt
                fixture.spool.acknowledge(receipt)
            assert fixture.spool.pending() == ()
            assert fixture.resolutions == 2
            assert fixture.native.commands == native_before
            fresh = fixture.new_core()
            await fresh.resume_executor_held_prompts(remote_ready=False)
            restored = await fresh.held_prompts("c_target")
            assert restored == pending
            assert [row["held_target_conversation_id"] for row in fixture.held_rows()] == [
                "c_target",
                "c_target",
            ]
            assert [row["held_state"] for row in fixture.held_rows()] == ["pending", "pending"]
            assert not await fixture.source_reply_credited(fresh)
            for (envelope, write), receipt in zip(entries, first_receipts, strict=True):
                assert await fixture.deliver(envelope, write, system=fresh) == receipt
            assert await fresh.held_prompts("c_target") == restored
            assert fixture.native.commands == native_before
        finally:
            await fixture.close()

    asyncio.run(run())


def test_idle_held_admission_resumes_after_restart_without_a_fresh_send(
    tmp_db: Connection,
    tmp_path: Path,
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("held_resume", "Resume original bytes Ω")
            receipt = await fixture.deliver(envelope, write)
            fixture.spool.acknowledge(receipt)
            assert not await fixture.source_reply_credited()
            fixture.system.pause_drain = True
            await fixture.system.interrupt("c_target")
            assert not await fixture.system.is_running("c_target")
            assert fixture.held_rows()[0]["held_state"] == "pending"
            fresh = fixture.new_core()
            native_before = len(fixture.native.writes())
            await fresh.resume_executor_held_prompts(remote_ready=False)
            await asyncio.sleep(0)
            assert len(fixture.native.writes()) == native_before
            await fresh.resume_executor_held_prompts(remote_ready=True)
            await eventually(
                lambda: fixture.held_rows()[0]["held_state"] == "consumed",
                "Restored idle admission did not resume after reconciliation",
            )
            assert await fresh.held_prompts("c_target") == ()
            assert len(fixture.native.writes()) == native_before + 1
            assert await fixture.source_reply_credited(fresh)
            assert await fixture.deliver(envelope, write, system=fresh) == receipt
            assert await fresh.held_prompts("c_target") == ()
        finally:
            await fixture.close()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["discard", "promote"])
def test_terminal_held_action_and_original_receipt_replay_never_resurrect(
    tmp_db: Connection,
    tmp_path: Path,
    action: str,
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("held_terminal", "Keep these exact bytes")
            receipt = await fixture.deliver(envelope, write)
            held = await fixture.system.held_prompts("c_target")
            assert len(held) == 1
            if action == "discard":
                assert await fixture.system.discard_held_prompt("c_target", held[0].held_prompt_id)
            else:
                fate = await fixture.system.promote_held_prompt(
                    "c_target",
                    held[0].held_prompt_id,
                    HeldPromptPromotionMode.send_now,
                )
                assert isinstance(fate, PromptDeliveryStarted)
            assert fixture.held_rows()[0]["held_state"] == "consumed"
            native_before = list(fixture.native.commands)
            fresh = fixture.new_core()
            await fresh.resume_executor_held_prompts(remote_ready=True)
            assert await fresh.held_prompts("c_target") == ()
            assert await fixture.deliver(envelope, write, system=fresh) == receipt
            assert await fresh.held_prompts("c_target") == ()
            assert fixture.native.commands == native_before
            assert await fixture.source_reply_credited(fresh) is (action == "promote")
        finally:
            await fixture.close()

    asyncio.run(run())


def test_interrupted_dispatch_preserves_bytes_quarantines_and_does_not_credit_reply(
    tmp_db: Connection,
    tmp_path: Path,
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("held_uncertain", "Never drop\nExact Ω bytes ")
            receipt = await fixture.deliver(envelope, write)
            fixture.spool.acknowledge(receipt)
            assert not await fixture.source_reply_credited()
            held = (await fixture.system.held_prompts("c_target"))[0]
            fixture.native.fail_next_write = True
            with pytest.raises(InjectedCrash, match="outcome interrupted"):
                await fixture.system.promote_held_prompt(
                    "c_target",
                    held.held_prompt_id,
                    HeldPromptPromotionMode.send_now,
                )
            live_unknown = await fixture.system.held_prompts("c_target")
            assert len(live_unknown) == 1 and live_unknown[0].delivery_uncertain
            attempts = len(fixture.native.writes())
            next_envelope, next_write = fixture.entry("held_live_after_unknown", "Wait live")
            assert (await fixture.deliver(next_envelope, next_write)).result["fate"] == "queued"
            live_pending = await fixture.system.held_prompts("c_target")
            assert isinstance(
                await fixture.system.promote_held_prompt(
                    "c_target", live_pending[1].held_prompt_id, HeldPromptPromotionMode.send_now
                ),
                PromptDeliveryQueued,
            )
            assert len(fixture.native.writes()) == attempts
            row = fixture.held_rows()[0]
            assert row["held_state"] == "dispatching"
            assert json.loads(row["held_prompt_json"])["content"] == encode_value(held.content)
            native_before = list(fixture.native.commands)
            fresh = fixture.new_core()
            restored = await fresh.held_prompts("c_target")
            assert len(restored) == 2
            assert restored[0].content == held.content
            assert restored[0].delivery_uncertain
            await fresh.resume_executor_held_prompts(remote_ready=True)
            await fresh.resume_executor_held_prompts(remote_ready=True)
            fate = await fresh.promote_held_prompt(
                "c_target",
                restored[0].held_prompt_id,
                HeldPromptPromotionMode.send_now,
            )
            assert isinstance(fate, PromptDeliveryUncertain)
            assert await fixture.deliver(envelope, write, system=fresh) == receipt
            assert fixture.native.commands == native_before
            assert not await fixture.source_reply_credited(fresh)
            assert fixture.held_rows()[0]["held_state"] == "dispatching"
            assert len(await fresh.held_prompts("c_target")) == 2
        finally:
            await fixture.close()

    asyncio.run(run())


@pytest.mark.parametrize("run_janitor", [False, True])
def test_automatic_uncertain_drain_never_discards_or_retries_even_after_idle_cleanup(
    tmp_db: Connection,
    tmp_path: Path,
    run_janitor: bool,
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("held_auto_uncertain", "Retain auto-drain Ω\nBytes ")
            receipt = await fixture.deliver(envelope, write)
            fixture.spool.acknowledge(receipt)
            original = (await fixture.system.held_prompts("c_target"))[0]
            assert not await fixture.source_reply_credited()
            fixture.system.pause_drain = True
            await fixture.system.interrupt("c_target")
            fresh = fixture.new_core()
            fixture.native.uncertain_next_write = True
            native_before = len(fixture.native.writes())
            await fresh.resume_executor_held_prompts(remote_ready=True)
            await eventually(
                lambda: len(fixture.native.writes()) > native_before,
                "Automatic drain never reached the native boundary",
            )
            # Wait for the core's retained settlement, rather than inspect the
            # short interval between dispatch marking and outcome processing.
            await fresh.wait_until_quiescent()
            uncertain = await fresh.held_prompts("c_target")
            assert len(uncertain) == 1
            assert uncertain[0].content == original.content
            assert uncertain[0].delivery_uncertain
            assert fixture.held_rows()[0]["held_state"] == "dispatching"
            assert json.loads(fixture.held_rows()[0]["held_prompt_json"])[
                "content"
            ] == encode_value(original.content)
            assert (
                tmp_db.execute(
                    "SELECT count(*) FROM conversation_events WHERE kind='prompt_discarded' "
                    "AND json_extract(payload,'$.sender_message_id')='held_auto_uncertain'"
                ).fetchone()[0]
                == 0
            )
            assert not await fixture.source_reply_credited(fresh)
            attempts = len(fixture.native.writes())
            if run_janitor:
                stops_before = sum(
                    command.operation is ChildOperation.stop for command in fixture.native.commands
                )
                await fresh.start_idle_child_janitor()
                await eventually(
                    lambda: (
                        sum(
                            command.operation is ChildOperation.stop
                            for command in fixture.native.commands
                        )
                        > stops_before
                    ),
                    "Idle cleanup did not stop the quarantined child",
                )
                await fresh.wait_until_quiescent()
                assert len(fixture.native.writes()) == attempts
                assert (await fresh.held_prompts("c_target"))[0].delivery_uncertain
            later_envelope, later_write = fixture.entry(
                "held_after_unknown", "Wait behind uncertainty"
            )
            later = await fixture.deliver(later_envelope, later_write, system=fresh)
            assert later.result["fate"] == "queued"
            assert len(fixture.native.writes()) == attempts
            assert [held.sender_message_id for held in await fresh.held_prompts("c_target")] == [
                "held_auto_uncertain",
                "held_after_unknown",
            ]
            live_pending = await fresh.held_prompts("c_target")
            assert isinstance(
                await fresh.promote_held_prompt(
                    "c_target",
                    live_pending[1].held_prompt_id,
                    HeldPromptPromotionMode.send_now,
                ),
                PromptDeliveryQueued,
            )
            assert len(fixture.native.writes()) == attempts
            restored = fixture.new_core()
            await restored.resume_executor_held_prompts(remote_ready=True)
            await restored.resume_executor_held_prompts(remote_ready=True)
            pending = await restored.held_prompts("c_target")
            assert len(pending) == 2 and pending[0].delivery_uncertain
            assert not pending[1].delivery_uncertain
            assert isinstance(
                await restored.promote_held_prompt(
                    "c_target",
                    pending[0].held_prompt_id,
                    HeldPromptPromotionMode.send_now,
                ),
                PromptDeliveryUncertain,
            )
            assert isinstance(
                await restored.promote_held_prompt(
                    "c_target",
                    pending[1].held_prompt_id,
                    HeldPromptPromotionMode.send_now,
                ),
                PromptDeliveryQueued,
            )
            assert len(fixture.native.writes()) == attempts
            assert await fixture.deliver(envelope, write, system=restored) == receipt
            assert not await fixture.source_reply_credited(restored)
        finally:
            await fixture.close()

    asyncio.run(run())


def test_known_safe_native_command_requeue_persists_new_id_order_and_original_bytes(
    tmp_db: Connection,
    tmp_path: Path,
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            first_envelope, first_write = fixture.entry(
                "held_native_command", "/scripted Ω\nExact "
            )
            first_receipt = await fixture.deliver(first_envelope, first_write)
            second_envelope, second_write = fixture.entry("held_other", "Other held message")
            await fixture.deliver(second_envelope, second_write)
            initial = await fixture.system.held_prompts("c_target")
            assert [held.sender_message_id for held in initial] == [
                "held_native_command",
                "held_other",
            ]
            fixture.native.refuse_next_steer = True
            fate = await fixture.system.promote_held_prompt(
                "c_target",
                initial[0].held_prompt_id,
                HeldPromptPromotionMode.steer,
            )
            assert isinstance(fate, PromptDeliveryQueued)
            requeued = await fixture.system.held_prompts("c_target")
            assert [held.sender_message_id for held in requeued] == [
                "held_other",
                "held_native_command",
            ]
            assert requeued[1].held_prompt_id != initial[0].held_prompt_id
            assert requeued[1].content == initial[0].content
            assert not any(held.delivery_uncertain for held in requeued)
            rows = fixture.held_rows()
            assert [row["submission_id"] for row in rows] == ["held_other", "held_native_command"]
            assert [row["held_state"] for row in rows] == ["pending", "pending"]
            for row, held in zip(rows, requeued, strict=True):
                persisted = json.loads(row["held_prompt_json"])
                assert persisted["held_prompt_id"] == held.held_prompt_id
                assert persisted["content"] == encode_value(held.content)
            assert not await fixture.source_reply_credited()
            native_before = list(fixture.native.commands)
            fresh = fixture.new_core()
            await fresh.resume_executor_held_prompts(remote_ready=True)
            assert await fresh.held_prompts("c_target") == requeued
            assert fixture.native.commands == native_before
            assert await fixture.deliver(first_envelope, first_write, system=fresh) == first_receipt
            assert await fresh.held_prompts("c_target") == requeued
        finally:
            await fixture.close()

    asyncio.run(run())


def test_kill_retains_unknown_held_bytes_and_discards_only_known_pending(
    tmp_db: Connection, tmp_path: Path
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("held_kill_unknown", "Unknown exact Ω bytes\n")
            await fixture.deliver(envelope, write)
            original = (await fixture.system.held_prompts("c_target"))[0]
            fixture.native.fail_next_write = True
            with pytest.raises(InjectedCrash):
                await fixture.system.promote_held_prompt(
                    "c_target", original.held_prompt_id, HeldPromptPromotionMode.send_now
                )
            fresh = fixture.new_core()
            later_envelope, later_write = fixture.entry("held_kill_pending", "Known pending")
            await fixture.deliver(later_envelope, later_write, system=fresh)
            attempts = len(fixture.native.writes())
            await fresh.kill("c_target")
            pending = await fresh.held_prompts("c_target")
            assert len(pending) == 1 and pending[0].delivery_uncertain
            assert pending[0].content == original.content
            states = {row["submission_id"]: row["held_state"] for row in fixture.held_rows()}
            assert states == {"held_kill_unknown": "dispatching", "held_kill_pending": "consumed"}
            discarded = tmp_db.execute(
                "SELECT json_extract(payload,'$.sender_message_id') FROM conversation_events "
                "WHERE kind='prompt_discarded'"
            ).fetchall()
            assert [row[0] for row in discarded] == ["held_kill_pending"]
            restored = fixture.new_core()
            await restored.resume_executor_held_prompts(remote_ready=True)
            assert await restored.held_prompts("c_target") == pending
            assert len(fixture.native.writes()) == attempts
            assert not await fixture.source_reply_credited(restored)
        finally:
            await fixture.close()

    asyncio.run(run())


@pytest.mark.parametrize("automatic", [False, True])
def test_cancelled_dispatch_marker_keeps_acknowledged_bytes_visible_and_gated(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, automatic: bool
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("held_marker_cancel", "Marker boundary exact Ω")
            await fixture.deliver(envelope, write)
            fixture.system.pause_drain = True
            await fixture.system.interrupt("c_target")
            fresh = fixture.new_core()
            original = fresh._store.mark_executor_held_dispatching
            marked = asyncio.Event()

            async def cancel_after_commit(target: str, sender_ids: tuple[str, ...]) -> bool:
                await original(target, sender_ids)
                marked.set()
                raise asyncio.CancelledError

            monkeypatch.setattr(fresh._store, "mark_executor_held_dispatching", cancel_after_commit)
            attempts = len(fixture.native.writes())
            if automatic:
                await fresh.resume_executor_held_prompts(remote_ready=True)
                await asyncio.wait_for(marked.wait(), 2)
                await asyncio.sleep(0)
            else:
                held = (await fresh.held_prompts("c_target"))[0]
                with pytest.raises(asyncio.CancelledError):
                    await fresh.promote_held_prompt(
                        "c_target", held.held_prompt_id, HeldPromptPromotionMode.send_now
                    )
            pending = await fresh.held_prompts("c_target")
            assert len(pending) == 1 and pending[0].delivery_uncertain
            assert fixture.held_rows()[0]["held_state"] == "dispatching"
            assert len(fixture.native.writes()) == attempts
            next_envelope, next_write = fixture.entry("held_after_marker", "Stay behind marker")
            assert (await fixture.deliver(next_envelope, next_write, system=fresh)).result[
                "fate"
            ] == "queued"
            assert len(await fresh.held_prompts("c_target")) == 2
            restored = fixture.new_core()
            await restored.resume_executor_held_prompts(remote_ready=True)
            restored_pending = await restored.held_prompts("c_target")
            assert len(restored_pending) == 2 and restored_pending[0].delivery_uncertain
            assert len(fixture.native.writes()) == attempts
        finally:
            await fixture.close()

    asyncio.run(run())


def test_native_steer_cancellation_preserves_live_held_unknown_and_never_retries(
    tmp_db: Connection, tmp_path: Path
) -> None:
    async def run() -> None:
        fixture = await boot(tmp_db, tmp_path)
        try:
            envelope, write = fixture.entry("held_native_cancel", "Cancelled steer Ω\nExact ")
            receipt = await fixture.deliver(envelope, write)
            held = (await fixture.system.held_prompts("c_target"))[0]
            fixture.native.cancel_next_steer = True
            with pytest.raises(asyncio.CancelledError):
                await fixture.system.promote_held_prompt(
                    "c_target", held.held_prompt_id, HeldPromptPromotionMode.steer
                )
            commands = list(fixture.native.commands)
            pending = await fixture.system.held_prompts("c_target")
            assert len(pending) == 1 and pending[0].delivery_uncertain
            assert pending[0].content == held.content
            assert fixture.held_rows()[0]["held_state"] == "dispatching"
            next_envelope, next_write = fixture.entry(
                "held_after_cancel", "Wait behind cancelled steer"
            )
            assert (await fixture.deliver(next_envelope, next_write)).result["fate"] == "queued"
            live_pending = await fixture.system.held_prompts("c_target")
            assert isinstance(
                await fixture.system.promote_held_prompt(
                    "c_target", live_pending[1].held_prompt_id, HeldPromptPromotionMode.steer
                ),
                PromptDeliveryQueued,
            )
            assert fixture.native.commands == commands
            assert not await fixture.source_reply_credited()
            fresh = fixture.new_core()
            await fresh.resume_executor_held_prompts(remote_ready=True)
            restored = await fresh.held_prompts("c_target")
            assert len(restored) == 2 and restored[0].delivery_uncertain
            assert restored[0].content == held.content
            assert await fixture.deliver(envelope, write, system=fresh) == receipt
            assert fixture.native.commands == commands
        finally:
            await fixture.close()

    asyncio.run(run())
