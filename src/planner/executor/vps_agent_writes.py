"""Interpret Mac agent writes and bind message dispatch to the inbox position."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, cast

from planner.conversation.api import delivery_fate_json
from planner.conversation.contracts import (
    ConversationSystem,
    NativeMessageAdmission,
    NativeMessageOperation,
)
from planner.conversation.events import (
    MessageToOwnerEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptDeliveryUncertainEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
)
from planner.conversation.model_resolution.contracts import CanonicalModelValidation
from planner.conversation.storage import ConversationStore
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.db import connect
from planner.execution_identity import ExecutorIdentity
from planner.executor.link_files import settled_io
from planner.executor.mutations import write_ticket_mutation
from planner.executor.protocol import (
    AcceptanceReceipt,
    CliWrite,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    encode_value,
    type_adapter,
)
from planner.message_delivery import api, service
from planner.message_delivery.contracts import MessageDeliveryMode

if TYPE_CHECKING:
    from planner.executor.vps_inbox import InboxStore
    from planner.executor.vps_runs import MacRuns


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class AgentWrites:
    def __init__(self, store: InboxStore, clock: Clock, runs: MacRuns) -> None:
        self.store = store
        self.db_path = store.db_path
        self.clock = clock
        self.runs = runs
        self.conversations: ConversationSystem

    async def accept(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        write = type_adapter(CliWrite).validate_python(envelope.payload)
        if write.child.conversation_id != envelope.conversation_id:
            raise ValueError("CLI conversation conflicts with envelope")
        if write.operation in ("message.send", "ticket.request_help"):
            principal = await settled_io(self.runs.authority, write.child)
            return await self._native_message(envelope, write, principal, identity)
        principal = await settled_io(self.runs.authority, write.child)
        from planner.tickets.contracts import ReviewTransferNotice
        from planner.tickets.review_transfer import notify_review_transfers

        notices: list[ReviewTransferNotice] = []

        def mutate(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
            # This read shares the receipt's BEGIN IMMEDIATE and domain write.
            # A separate post-probe read cannot fence closure committed while
            # the receipt transaction waits for its SQLite write lock.
            current_assignment = connection.execute(
                "SELECT principal_kind,principal_id FROM executor_child_assignments "
                "WHERE executor_incarnation=? AND child_id=? AND conversation_id=? "
                "AND closed_at_ms IS NULL",
                (
                    write.child.executor_incarnation,
                    write.child.child_id,
                    write.child.conversation_id,
                ),
            ).fetchone()
            if current_assignment is None or tuple(current_assignment) != (
                principal.kind.value,
                principal.id,
            ):
                raise PlannerError(ErrorCode.agent_forbidden, "child assignment changed", {})
            creation_validation: CanonicalModelValidation | None = None
            if write.operation == "ticket.create":
                from planner.runtime.conversation_start import refuse_known_incompatibility
                from planner.tickets.api import resolve_ticket_creation_validation

                body = write.arguments.get("body")
                if not isinstance(body, dict):
                    raise PlannerError(ErrorCode.validation, "ticket mutation needs a body", {})
                creation_validation = resolve_ticket_creation_validation(
                    body, connection, principal=principal
                )
                refuse_known_incompatibility(creation_validation)
            return write_ticket_mutation(
                connection,
                write,
                principal,
                self.clock,
                review_transfer_notices=notices,
                creation_validation=creation_validation,
            )

        receipt = await settled_io(
            partial(self.store.accept, envelope, mutate, now_ms(), identity=identity)
        )
        if notices:
            connection = connect(self.db_path)
            try:
                await notify_review_transfers(
                    self.conversations, connection, notices, now=self.clock.now_unix()
                )
            finally:
                connection.close()
        return receipt

    def _resolve_native_recipient(
        self, conn: sqlite3.Connection, write: CliWrite, principal: Principal
    ) -> Principal:
        from planner.message_delivery import api

        body = write.arguments.get("body")
        if not isinstance(body, dict):
            raise PlannerError(ErrorCode.validation, "message body must be an object", {})
        if write.operation == "message.send":
            return api._principal(body.get("target"))
        default = self._help_recipient(conn, write, principal)
        if set(body) - {"message", "recipient"}:
            raise PlannerError(ErrorCode.validation, "unknown help request field", {})
        return api._principal(body["recipient"]) if body.get("recipient") else default

    def _help_recipient(
        self, conn: sqlite3.Connection, write: CliWrite, principal: Principal
    ) -> Principal:
        from planner.tickets import data

        ticket_id = write.arguments.get("ticket_id")
        if not isinstance(ticket_id, str) or principal != Principal(
            PrincipalKind.ticket, ticket_id
        ):
            raise PlannerError(
                ErrorCode.agent_forbidden, "only the Ticket Worker can request help", {}
            )
        return data.read_ticket(conn, ticket_id).ceiling_holder

    async def _native_message(
        self,
        envelope: QueuedEnvelope,
        write: CliWrite,
        principal: Principal,
        identity: ExecutorIdentity,
    ) -> AcceptanceReceipt:
        return await deliver_native_message(
            store=self.store,
            envelope=envelope,
            write=write,
            principal=principal,
            identity=identity,
            clock=self.clock,
            conversations=self.conversations,
            resolve_recipient=self._resolve_native_recipient,
        )


type RecipientResolver = Callable[[sqlite3.Connection, CliWrite, Principal], Principal]


async def deliver_native_message(
    *,
    store: InboxStore,
    envelope: QueuedEnvelope,
    write: CliWrite,
    principal: Principal,
    identity: ExecutorIdentity,
    clock: Clock,
    conversations: ConversationSystem,
    resolve_recipient: RecipientResolver,
) -> AcceptanceReceipt:
    """Reserve dispatch before calling a provider; a replay never calls it again."""
    prior = await settled_io(partial(store.preflight, envelope, identity=identity))
    if prior is not None:
        return prior

    def admit() -> tuple[AcceptanceReceipt | None, Principal, str, MessageDeliveryMode]:
        with store._transaction() as conn:
            previous = store._preflight(conn, envelope, identity)
            if previous is not None:
                return previous, principal, "", MessageDeliveryMode.queue
            body = write.arguments.get("body")
            if not isinstance(body, dict):
                raise PlannerError(ErrorCode.validation, "message body must be an object", {})
            message = api._message_text(body.get("message"))
            if write.operation == "ticket.request_help" and set(body) - {"message", "recipient"}:
                raise PlannerError(ErrorCode.validation, "unknown help request field", {})
            mode = (
                api._message_delivery_mode(body["mode"])
                if "mode" in body
                else api._message_delivery_mode()
            )
            recipient = resolve_recipient(conn, write, principal)
            service.require_reach(conn, principal, recipient)
            store.install_files(envelope.file_dependencies)
            result: JsonObject = {
                "target": {"kind": recipient.kind.value, "id": recipient.id},
                "conversation_id": None,
                "fate": "uncertain",
            }
            reservation = AcceptanceReceipt(
                envelope.sender_id,
                envelope.submission_id,
                envelope.payload_sha256,
                ReceiptDisposition.uncertain,
                result,
                now_ms(),
            )
            conn.execute(
                "INSERT INTO mac_inbox_positions VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(sender_id,conversation_id) DO UPDATE SET "
                "sequence=excluded.sequence,submission_id=excluded.submission_id,"
                "disposition=excluded.disposition,result_json=excluded.result_json",
                (
                    envelope.sender_id,
                    envelope.conversation_id,
                    envelope.sequence,
                    envelope.submission_id,
                    "dispatching",
                    json.dumps(encode_value(reservation)),
                ),
            )
            return None, recipient, message, mode

    previous, recipient, message, mode = await settled_io(admit)
    if previous is not None:
        return previous

    def complete(conn: sqlite3.Connection, result: JsonObject) -> AcceptanceReceipt:
        previous = conn.execute(
            "SELECT * FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?",
            (envelope.sender_id, envelope.conversation_id),
        ).fetchone()
        if previous is None or previous["submission_id"] != envelope.submission_id:
            raise ValueError("message inbox position changed during dispatch")
        if previous["disposition"] != "dispatching":
            return store._receipt(previous)
        disposition = (
            ReceiptDisposition.refused
            if "error" in result
            else ReceiptDisposition.uncertain
            if result.get("fate") == "uncertain"
            else ReceiptDisposition.accepted
        )
        if disposition is ReceiptDisposition.refused:
            from planner.executor.refusals import record_refused_write

            record_refused_write(conn, store.db_path, envelope, result)
        receipt = AcceptanceReceipt(
            envelope.sender_id,
            envelope.submission_id,
            envelope.payload_sha256,
            disposition,
            result,
            now_ms(),
        )
        conn.execute(
            "UPDATE mac_inbox_positions SET disposition=?,result_json=? "
            "WHERE sender_id=? AND conversation_id=?",
            (
                disposition.value,
                json.dumps(encode_value(receipt)),
                envelope.sender_id,
                envelope.conversation_id,
            ),
        )
        return receipt

    operation = NativeMessageOperation(envelope.sender_id, envelope.submission_id)

    def recorded(
        conn: sqlite3.Connection,
        conversation_id: str,
        payload: object,
        admission: NativeMessageAdmission | None,
    ) -> None:
        composed = False
        if isinstance(payload, MessageToOwnerEventPayload):
            fate: JsonObject = {"fate": "recorded"}
            composed = True
        elif isinstance(payload, PromptEventPayload) and admission is not None:
            fate = cast(JsonObject, delivery_fate_json(admission.fate))
            composed = admission.composed_content_delivered
        elif isinstance(payload, PromptDeliveryUncertainEventPayload):
            fate = {"fate": "uncertain"}
            composed = admission.composed_content_delivered if admission else False
        elif isinstance(payload, PromptDeliveryRefusedEventPayload):
            fate = {"fate": "refused", "refusal_reason": payload.refusal_reason.value}
        elif isinstance(payload, PromptDiscardedEventPayload):
            fate = {"fate": "discarded"}
        else:
            return
        if composed and write.turn is not None:
            ConversationStore.credit_reply_in_transaction(conn, write.turn, recipient)
        operation.composed_confirmed = composed
        current = conn.execute(
            "SELECT submission_id,disposition FROM mac_inbox_positions "
            "WHERE sender_id=? AND conversation_id=?",
            (envelope.sender_id, envelope.conversation_id),
        ).fetchone()
        if current is None or current[0] != envelope.submission_id or current[1] != "dispatching":
            return
        complete(
            conn,
            {
                "target": {"kind": recipient.kind.value, "id": recipient.id},
                "conversation_id": conversation_id,
                **fate,
            },
        )

    operation.in_commit = recorded
    connection = connect(store.db_path)
    try:
        try:
            delivered = await service.send_message(
                conversations,
                connection,
                clock,
                RequestContext(principal),
                recipient,
                message,
                mode,
                sender_message_id=envelope.submission_id,
                native_message_operation=operation,
            )
            result = cast(JsonObject, api._result_json(delivered))
        except PlannerError as error:
            result = cast(JsonObject, error.to_payload())
    finally:
        connection.close()

    def save() -> AcceptanceReceipt:
        with store._transaction() as conn:
            store.runs._connected(conn, identity)
            return complete(conn, result)

    return await settled_io(save)
