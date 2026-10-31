"""Interpret Mac agent writes and bind message dispatch to the inbox position."""

from __future__ import annotations

import sqlite3
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
from planner.conversation.storage import ConversationStore
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.db import connect
from planner.execution_identity import ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.link_files import settled_io
from planner.executor.protocol import (
    AcceptanceReceipt,
    CliWrite,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    type_adapter,
)
from planner.message_delivery import api, service
from planner.message_delivery.contracts import MessageDeliveryMode
from planner.tickets import actions, views

if TYPE_CHECKING:
    from planner.executor.vps_inbox import InboxStore
    from planner.executor.vps_runs import MacRuns
    from planner.tickets.contracts import ReviewTransferNotice


def write_ticket_mutation(
    connection: sqlite3.Connection,
    write: CliWrite,
    principal: Principal,
    clock: Clock,
    *,
    review_transfer_notices: list[ReviewTransferNotice] | None = None,
) -> JsonObject:
    """Effect and receipt use the caller's open transaction; no native I/O here."""
    from planner.tickets.api import write_create_ticket, write_patch_ticket

    args = write.arguments
    body = args.get("body")
    if not isinstance(body, dict):
        raise PlannerError(ErrorCode.validation, "ticket mutation needs a body", {})
    if write.operation == "feedback.use":
        from planner.feedback.api import write_use_feedback

        return write_use_feedback(body, connection, clock, RequestContext(principal))
    if write.operation in ("ticket.block", "ticket.unblock"):
        from planner.membership.collections import ENTRIES, MembershipWrite
        from planner.membership.contracts import Collection

        ticket_id = args.get("ticket_id")
        blocker_id = args.get("blocker_id")
        if not isinstance(ticket_id, str) or not isinstance(blocker_id, str):
            raise PlannerError(ErrorCode.validation, "blocker IDs must be text", {})
        entry = ENTRIES[Collection.blockers]
        membership = MembershipWrite(
            conn=connection,
            principal=principal,
            container_id=ticket_id,
            member_id=blocker_id,
            now=clock.now_unix(),
            admit=partial(entry.rule, connection, RequestContext(principal), ticket_id, blocker_id),
        )
        (entry.add if write.operation == "ticket.block" else entry.remove)(membership)
        return {
            "collection": "blockers",
            "container_id": ticket_id,
            "member_id": blocker_id,
            "ok": True,
        }
    if write.operation == "ticket.create":
        return write_create_ticket(
            body,
            connection,
            RequestContext(principal),
            clock,
        )
    ticket_id = args.get("ticket_id")
    if not isinstance(ticket_id, str):
        raise PlannerError(ErrorCode.validation, "ticket mutation needs ticket_id", {})
    if write.operation == "ticket.propose":
        if principal != Principal(PrincipalKind.ticket, ticket_id):
            raise PlannerError(
                ErrorCode.agent_forbidden, "only the Ticket's Worker can propose", {}
            )
        text = body.get("body")
        if not isinstance(text, str):
            raise PlannerError(ErrorCode.validation, "proposal body must be text", {})
        ticket = actions.file_current_proposal(
            connection, ticket_id, body=text, ctx=RequestContext(principal), clock=clock
        )
        return views.ticket_json(ticket, clock.now_unix())
    if write.operation == "ticket.edit":
        return write_patch_ticket(
            ticket_id,
            body,
            connection,
            RequestContext(principal),
            clock,
            review_transfer_notices=review_transfer_notices,
        )
    raise PlannerError(ErrorCode.validation, "unsupported transactional executor mutation", {})


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
        principal = await settled_io(self.runs.authority, write.child)
        if write.operation in ("message.send", "ticket.request_help"):
            return await self._deliver_message(envelope, write, principal, identity)
        from planner.tickets.review_transfer import notify_review_transfers

        notices: list[ReviewTransferNotice] = []

        def mutate(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
            # This read shares the receipt's BEGIN IMMEDIATE and domain write.
            # A separate post-probe read cannot fence closure committed while
            # the receipt transaction waits for its SQLite write lock.
            current_assignment = self.store.runs.open_child_assignment(connection, write.child)
            if current_assignment != (
                principal.kind.value,
                principal.id,
            ):
                raise PlannerError(ErrorCode.agent_forbidden, "child assignment changed", {})
            return write_ticket_mutation(
                connection,
                write,
                principal,
                self.clock,
                review_transfer_notices=notices,
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

    async def _deliver_message(
        self,
        envelope: QueuedEnvelope,
        write: CliWrite,
        principal: Principal,
        identity: ExecutorIdentity,
    ) -> AcceptanceReceipt:
        """Reserve before calling a provider; a replay never calls it again."""
        previous, recipient, message, mode = await settled_io(
            self._reserve_dispatch, envelope, write, principal, identity
        )
        if previous is not None:
            return previous
        operation = NativeMessageOperation(envelope.sender_id, envelope.submission_id)
        operation.in_commit = partial(self._record_delivery, envelope, write, recipient, operation)
        connection = connect(self.db_path)
        try:
            try:
                delivered = await service.send_message(
                    self.conversations,
                    connection,
                    self.clock,
                    RequestContext(principal),
                    recipient,
                    message,
                    mode,
                    sender_message_id=envelope.submission_id,
                    recording_receipt=operation,
                )
                result = cast(JsonObject, api._result_json(delivered))
            except PlannerError as error:
                result = cast(JsonObject, error.to_payload())
        finally:
            connection.close()

        def save() -> AcceptanceReceipt:
            with self.store._transaction() as conn:
                self.store.runs._connected(conn, identity)
                return self._finish_dispatch(conn, envelope, result)

        return await settled_io(save)

    def _reserve_dispatch(
        self,
        envelope: QueuedEnvelope,
        write: CliWrite,
        principal: Principal,
        identity: ExecutorIdentity,
    ) -> tuple[AcceptanceReceipt | None, Principal, str, MessageDeliveryMode]:
        with self.store._transaction() as conn:
            previous = self.store._prior_receipt(conn, envelope, identity)
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
            if write.operation == "message.send":
                recipient = api._principal(body.get("target"))
            else:
                default = self._help_recipient(conn, write, principal)
                recipient = api._principal(body["recipient"]) if body.get("recipient") else default
            service.require_reach(conn, principal, recipient)
            self.store.install_files(envelope.file_dependencies)
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
            self.store._write_position(conn, envelope, reservation, dispatching=True)
            return None, recipient, message, mode

    def _finish_dispatch(
        self, conn: sqlite3.Connection, envelope: QueuedEnvelope, result: JsonObject
    ) -> AcceptanceReceipt:
        previous = conn.execute(
            "SELECT * FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?",
            (envelope.sender_id, envelope.conversation_id),
        ).fetchone()
        if previous is None or previous["submission_id"] != envelope.submission_id:
            raise ValueError("message inbox position changed during dispatch")
        if previous["disposition"] != "dispatching":
            return self.store._receipt(previous)
        disposition = (
            ReceiptDisposition.refused
            if "error" in result
            else ReceiptDisposition.uncertain
            if result.get("fate") == "uncertain"
            else ReceiptDisposition.accepted
        )
        if disposition is ReceiptDisposition.refused:
            from planner.executor.refusals import record_refused_write

            record_refused_write(conn, self.db_path, envelope, result)
        receipt = AcceptanceReceipt(
            envelope.sender_id,
            envelope.submission_id,
            envelope.payload_sha256,
            disposition,
            result,
            now_ms(),
        )
        self.store._write_position(conn, envelope, receipt)
        return receipt

    def _record_delivery(
        self,
        envelope: QueuedEnvelope,
        write: CliWrite,
        recipient: Principal,
        operation: NativeMessageOperation,
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
        self._finish_dispatch(
            conn,
            envelope,
            {
                "target": {"kind": recipient.kind.value, "id": recipient.id},
                "conversation_id": conversation_id,
                **fate,
            },
        )
