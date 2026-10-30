"""Crash between canonical delivery and executor receipt preserves the first target."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from tests.support.executor_catalog import ScriptedCatalogObserver
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.mac_outbox import MacOutbox
from planner.executor.native_messages import bound_native_principal, deliver_native_message
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    CliWrite,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    JsonObject,
    OutboxEntry,
    ReceiptDisposition,
    encode_value,
)
from planner.executor.remote import RemoteBackendChild
from planner.executor.store import ExecutorStore
from planner.tickets import data


def _configure_scoped_metadata(
    system: SqliteProcessConversationSystem,
    path: str,
    identity: ExecutorIdentity,
) -> None:
    from planner.conversation.model_resolution.contracts import (
        CatalogWorkspaceBinding,
        LaunchContext,
    )
    from planner.conversation.snapshot import BackendSnapshot, BackendSnapshotService
    from planner.tickets.contracts import TicketLocale

    class NoUnscopedProbe(BackendSnapshotService):
        def __init__(self) -> None:
            pass

        async def snapshot(
            self,
            backend_key: ConversationBackendKey,
            *,
            refresh: bool = False,
            discover_update_advisory: bool = True,
        ) -> BackendSnapshot:
            raise AssertionError("This lifecycle requires scoped metadata")

    class Observer(ScriptedCatalogObserver):
        async def capture_context(
            self,
            backend_key: ConversationBackendKey,
            *,
            workspace: CatalogWorkspaceBinding,
        ) -> LaunchContext:
            return replace(
                await super().capture_context(backend_key, workspace=workspace),
                destination=TicketLocale.vps,
                executor_identity=None,
            )

        def verify_context(self, expected: LaunchContext) -> bool:
            return self.context_valid and expected.executor_identity is None

    observer = Observer(lambda: identity)
    observer.model = "scripted"
    observer.efforts = ()
    system.configure_destination_validation(observer)


class SimulatedCrash(BaseException):
    pass


class CrashAfterCanonicalOwnerMessage(SqliteProcessConversationSystem):
    crash = True
    crash_before_owner_record = False
    owner_deliveries = 0

    async def record_message_to_owner(
        self, conversation_id: str, content: MessageContent, **kwargs: Any
    ) -> None:
        if self.crash_before_owner_record:
            raise SimulatedCrash("process died after dispatch marker before outcome")
        await super().record_message_to_owner(conversation_id, content, **kwargs)
        self.owner_deliveries += 1
        if self.crash:
            raise SimulatedCrash("domain committed; process died before executor receipt")


@pytest.mark.parametrize("committed", [True, False])
def test_help_replay_after_domain_commit_keeps_original_holder_and_source_turn(
    tmp_db: Connection,
    tmp_path: Path,
    committed: bool,
) -> None:
    async def run() -> None:
        path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
        store = ExecutorStore(path)
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1
        )
        store.begin_connection(identity, 1000)
        store.mark_ready(identity, 1001)
        ticket = data.create_ticket(
            tmp_db,
            title="Native message source",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            brief="Brief",
            employee_backend="codex",
            employee_launch_model="scripted",
            selection_kind="concrete",
        )
        sender = Principal(PrincipalKind.ticket, ticket.id)
        reference = ChildReference("incarnation", "child", "c_source")
        native_calls: list[ChildOperation] = []

        async def native(command: ChildCommand) -> CommandOutcome:
            native_calls.append(command.operation)
            result: JsonObject = (
                {"kind": "prompt_accepted", "composed_content_delivered": True}
                if command.operation is ChildOperation.write_prompt
                else {}
            )
            outcome = CommandOutcome(
                command.command_id, command.child, CommandDisposition.succeeded, result
            )
            # Model the actual endpoint's definitive receipt settlement, not a hint.
            outcome_conn = connect(path)
            try:
                outcome_conn.execute("BEGIN IMMEDIATE")
                store.record_command_outcome_in_transaction(outcome_conn, outcome)
                outcome_conn.commit()
            finally:
                outcome_conn.close()
            return outcome

        def factory(**kwargs: Any) -> RemoteBackendChild:
            return RemoteBackendChild(reference, native, lambda start: canonical_snapshot())

        system = CrashAfterCanonicalOwnerMessage(
            store=ConversationStore(path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(path),
        )
        _configure_scoped_metadata(system, path, identity)
        system.crash_before_owner_record = not committed
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    "c_source",
                    "scripted",
                    role_materials=ConversationRoleMaterials(
                        "Test",
                        (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id)),
                    ),
                )
            )
            data.write_ticket_conversation_start(
                tmp_db,
                ticket.id,
                conversation_id="c_source",
                backend="codex",
                model="scripted",
                reasoning_effort=None,
                now=2,
            )
            await system.send(
                "c_source",
                text_message_content("Answer the owner"),
                sender_label="owner",
                sender=OWNER_PRINCIPAL,
                recipient=sender,
            )
            write = CliWrite(
                "ticket.request_help",
                {
                    "ticket_id": ticket.id,
                    "body": {"message": "Please help with this decision."},
                },
                reference,
                TurnToken("c_source", 1),
            )
            spool = MacOutbox(tmp_path / "spool")
            payload = encode_value(write)
            assert isinstance(payload, dict)
            envelope = spool.add(
                OutboxEntry(
                    conversation_id="c_source",
                    kind=EnvelopeKind.cli_write,
                    payload=payload,
                    submission_id="native_help",
                )
            )
            resolutions: list[Principal] = []

            def recipient(
                connection: Connection, request: CliWrite, principal: Principal
            ) -> Principal:
                assert principal == sender
                holder = data.read_ticket(connection, ticket.id).ceiling_holder
                resolutions.append(holder)
                return holder

            async def deliver() -> object:
                return await deliver_native_message(
                    store=store,
                    envelope=envelope,
                    write=write,
                    principal=sender,
                    identity=identity,
                    clock=RealClock(),
                    conversations=system,
                    resolve_recipient=recipient,
                )

            with pytest.raises(SimulatedCrash):
                await deliver()
            assert store.lookup_receipt(envelope.sender_id, envelope.submission_id) is None
            connection = connect(path)
            try:
                assert connection.execute(
                    "SELECT state FROM executor_native_messages WHERE submission_id=?",
                    (envelope.submission_id,),
                ).fetchone()[0] == ("completed" if committed else "dispatching")
            finally:
                connection.close()
            domain = await system._store.sender_message_outcome("c_source", "native_help")
            if committed:
                assert domain is not None and domain.kind.value == "message_to_owner"
            else:
                assert domain is None
            metadata = await system._store.read_executor_turn("c_source")
            assert metadata is not None
            assert (
                {"kind": "owner", "id": "owner"}
                in json.loads(metadata[2])["explicit_reply_recipients"]
            ) is committed
            changed = data.accept_proposal(
                tmp_db,
                ticket.id,
                field="brief",
                principal=OWNER_PRINCIPAL,
                now=3,
                next_ceiling="needs_success_condition",
                next_holder=Principal(PrincipalKind.chief, "chief"),
            )
            assert changed.ceiling_holder != OWNER_PRINCIPAL
            assert bound_native_principal(store, envelope) == sender
            system.crash = False
            system.crash_before_owner_record = False
            native_before = list(native_calls)
            recovered = await deliver_native_message(
                store=ExecutorStore(path),
                envelope=envelope,
                write=write,
                principal=sender,
                identity=identity,
                clock=RealClock(),
                conversations=system,
                resolve_recipient=recipient,
            )
            assert recovered.disposition is (
                ReceiptDisposition.accepted if committed else ReceiptDisposition.uncertain
            )
            assert recovered.result == {
                "target": {"kind": "owner", "id": "owner"},
                "conversation_id": "c_source" if committed else None,
                "fate": "recorded" if committed else "uncertain",
            }
            metadata_after_replay = await system._store.read_executor_turn("c_source")
            assert metadata_after_replay is not None
            assert (
                {"kind": "owner", "id": "owner"}
                in json.loads(metadata_after_replay[2])["explicit_reply_recipients"]
            ) is committed
            assert resolutions == [OWNER_PRINCIPAL]
            assert system.owner_deliveries == int(committed)
            assert native_calls == native_before
            rows = tmp_db.execute(
                "SELECT count(*) FROM conversation_events "
                "WHERE json_extract(payload,'$.sender_message_id')='native_help'"
            ).fetchone()[0]
            assert rows == int(committed)
            spool.close()
        finally:
            for state in system._conversations.values():
                state.child = None
            await system.shutdown()

    asyncio.run(run())


def test_idle_target_steer_records_actual_started_fate_before_receipt_and_replay(
    tmp_db: Connection,
    tmp_path: Path,
) -> None:
    from planner.conversation.contracts import AddressedPromptDeliveryReceipt, PromptDeliveryStarted

    class CrashAfterPrompt(SqliteProcessConversationSystem):
        observed: list[AddressedPromptDeliveryReceipt]

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.observed = []

        async def send_with_receipt(
            self, conversation_id: str, content: MessageContent, **kwargs: Any
        ) -> AddressedPromptDeliveryReceipt:
            receipt = await super().send_with_receipt(conversation_id, content, **kwargs)
            self.observed.append(receipt)
            raise SimulatedCrash("actual native admission committed before executor receipt")

    async def run() -> None:
        path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
        store = ExecutorStore(path)
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1
        )
        store.begin_connection(identity, 1000)
        store.mark_ready(identity, 1001)
        target = data.create_ticket(
            tmp_db,
            title="Idle target",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            employee_backend="codex",
            employee_launch_model="scripted",
            selection_kind="concrete",
        )
        recipient = Principal(PrincipalKind.ticket, target.id)
        sender = Principal(PrincipalKind.chief, "chief")
        native_calls: list[ChildOperation] = []
        reference = ChildReference("incarnation", "target_child", "c_idle")

        async def native(command: ChildCommand) -> CommandOutcome:
            native_calls.append(command.operation)
            result: JsonObject = (
                {"kind": "prompt_accepted", "composed_content_delivered": True}
                if command.operation is ChildOperation.write_prompt
                else {}
            )
            outcome = CommandOutcome(
                command.command_id, command.child, CommandDisposition.succeeded, result
            )
            # Model the actual endpoint's definitive receipt settlement, not a hint.
            outcome_conn = connect(path)
            try:
                outcome_conn.execute("BEGIN IMMEDIATE")
                store.record_command_outcome_in_transaction(outcome_conn, outcome)
                outcome_conn.commit()
            finally:
                outcome_conn.close()
            return outcome

        def factory(**kwargs: Any) -> RemoteBackendChild:
            return RemoteBackendChild(reference, native, lambda start: canonical_snapshot())

        system = CrashAfterPrompt(
            store=ConversationStore(path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(path),
        )
        _configure_scoped_metadata(system, path, identity)
        spool = MacOutbox(tmp_path / "spool")
        try:
            await system.start_conversation(ConversationStartRequest("c_idle", "scripted"))
            data.write_ticket_conversation_start(
                tmp_db,
                target.id,
                conversation_id="c_idle",
                backend="codex",
                model="scripted",
                reasoning_effort=None,
                now=2,
            )
            assert not await system.is_running("c_idle")
            write = CliWrite(
                "message.send",
                {
                    "body": {
                        "target": {"kind": "ticket", "id": target.id},
                        "message": "Begin this idle turn",
                        "mode": "steer",
                    }
                },
                ChildReference("incarnation", "sender_child", "c_source"),
                None,
            )
            payload = encode_value(write)
            assert isinstance(payload, dict)
            envelope = spool.add(
                OutboxEntry(
                    conversation_id="c_source",
                    kind=EnvelopeKind.cli_write,
                    payload=payload,
                    submission_id="idle_steer",
                )
            )

            def resolve(
                connection: Connection, request: CliWrite, principal: Principal
            ) -> Principal:
                return recipient

            with pytest.raises(SimulatedCrash):
                await deliver_native_message(
                    store=store,
                    envelope=envelope,
                    write=write,
                    principal=sender,
                    identity=identity,
                    clock=RealClock(),
                    conversations=system,
                    resolve_recipient=resolve,
                )
            assert len(system.observed) == 1
            assert isinstance(system.observed[0].fate, PromptDeliveryStarted)
            assert store.lookup_receipt(envelope.sender_id, envelope.submission_id) is None
            before_replay = list(native_calls)
            events_before_replay = await system._store.read_events_after("c_idle", 0)
            receipt = await deliver_native_message(
                store=ExecutorStore(path),
                envelope=envelope,
                write=write,
                principal=sender,
                identity=identity,
                clock=RealClock(),
                conversations=system,
                resolve_recipient=resolve,
            )
            assert receipt.result == {
                "target": {"kind": "ticket", "id": target.id},
                "conversation_id": "c_idle",
                "fate": "started",
            }
            assert native_calls == before_replay
            assert len(system.observed) == 1
            assert await system._store.read_events_after("c_idle", 0) == events_before_replay
            from planner.conversation.events import PromptEventPayload

            assert (
                sum(isinstance(event.payload, PromptEventPayload) for event in events_before_replay)
                == 1
            )
        finally:
            spool.close()
            for state in system._conversations.values():
                state.child = None
            await system.shutdown()

    asyncio.run(run())
