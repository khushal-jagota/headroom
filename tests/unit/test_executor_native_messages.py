"""Crash between canonical delivery and executor receipt preserves the first target."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
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
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.mac_outbox import MacOutbox
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
from planner.executor.vps_agent_writes import deliver_native_message
from planner.executor.vps_inbox import InboxStore
from planner.tickets import data


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
        store = InboxStore(path)
        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1
        )
        store.runs.begin_connection(identity)
        store.runs.mark_ready(identity)
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
            return outcome

        from tests.support.executor_turns import MacRemoteChildFactory

        factory = MacRemoteChildFactory(
            lambda start, sink: reference, native, lambda start: canonical_snapshot(), db_path=path
        )

        system = CrashAfterCanonicalOwnerMessage(
            store=ConversationStore(path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(path),
        )
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
            saved = store.lookup_receipt(envelope.sender_id, envelope.submission_id)
            assert saved is not None
            assert saved.result["fate"] == ("recorded" if committed else "uncertain")
            assert (
                bool(
                    tmp_db.execute(
                        "SELECT 1 FROM conversation_reply_credits WHERE conversation_id='c_source' "
                        "AND principal_kind='owner' AND principal_id='owner'"
                    ).fetchone()
                )
                is committed
            )
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
            system.crash = False
            system.crash_before_owner_record = False
            native_before = list(native_calls)
            recovered = await deliver_native_message(
                store=InboxStore(path),
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
            assert (
                bool(
                    tmp_db.execute(
                        "SELECT 1 FROM conversation_reply_credits WHERE conversation_id='c_source' "
                        "AND principal_kind='owner' AND principal_id='owner'"
                    ).fetchone()
                )
                is committed
            )
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
