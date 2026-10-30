"""Bind one incoming native message to its original target and canonical outcome.

The incoming operation record lives in the authoritative VPS database. It is not
an outbound queue. A dispatched operation with no canonical outcome is uncertain;
recovery never sends it to another holder or native child.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from functools import partial
from typing import cast

from pydantic import TypeAdapter

from planner.conversation.api import delivery_fate_json
from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationSystem,
    NativeMessageAdmission,
    NativeMessageOperation,
    PromptDeliveryInjected,
    PromptDeliveryStarted,
)
from planner.conversation.events import (
    ConversationEventPayload,
    MessageToOwnerEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptDeliveryUncertainEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
)
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import ErrorCode, PlannerError, Principal
from planner.executor.contracts import (
    AcceptanceReceipt,
    CliWrite,
    ExecutorIdentity,
    JsonObject,
    QueuedEnvelope,
)
from planner.executor.store import ExecutorStore
from planner.executor.transfers import settled_io
from planner.message_delivery import api, service
from planner.message_delivery.contracts import MessageDeliveryMode

type RecipientResolver = Callable[[sqlite3.Connection, CliWrite, Principal], Principal]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _principal_json(principal: Principal) -> str:
    return _json({"kind": principal.kind.value, "id": principal.id})


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _lookup(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT * FROM executor_native_messages WHERE sender_id=? AND submission_id=?",
        (envelope.sender_id, envelope.submission_id),
    ).fetchone()
    if row is not None and row["payload_sha256"] != envelope.payload_sha256:
        raise PlannerError(ErrorCode.validation, "Native message identity conflicts", {})
    return cast(sqlite3.Row | None, row)


def bound_native_principal(store: ExecutorStore, envelope: QueuedEnvelope) -> Principal | None:
    """Recover original authority before a changed child assignment is consulted.

    The caller must still execute fenced preflight before returning any outcome.
    This returns no authority for a different digest or unbound operation.
    """
    with store._transaction() as connection:
        row = _lookup(connection, envelope)
        return TypeAdapter(Principal).validate_json(row["principal_json"]) if row else None


def _credit_reply(connection: sqlite3.Connection, row: sqlite3.Row, result: JsonObject) -> None:
    if row["source_turn_json"] is None or result.get("fate") == "refused":
        return
    from planner.conversation.storage import ConversationStore

    ConversationStore.credit_executor_reply_in_transaction(
        connection,
        TypeAdapter(TurnToken).validate_json(row["source_turn_json"]),
        TypeAdapter(Principal).validate_json(row["recipient_json"]),
    )


def _save_result(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    result: JsonObject,
    *,
    composed_content_delivered: bool = False,
) -> None:
    if composed_content_delivered:
        _credit_reply(connection, row, result)
    if row["result_json"] is not None and row["state"] == "completed":
        return
    state = "bound" if result.get("fate") == "queued" else "completed"
    connection.execute(
        "UPDATE executor_native_messages SET result_json=?,state=? "
        "WHERE sender_id=? AND submission_id=?",
        (_json(result), state, row["sender_id"], row["submission_id"]),
    )


def _message_operations(
    connection: sqlite3.Connection,
    conversation_id: str,
    submission_id: str | None,
    sender: Principal | None,
    recipient: Principal | None,
    *,
    native_message_operation: NativeMessageOperation | None = None,
) -> tuple[sqlite3.Row, ...]:
    if submission_id is None or sender is None or recipient is None:
        return ()
    operation = native_message_operation
    if operation is not None and operation.submission_id == submission_id:
        row = connection.execute(
            "SELECT * FROM executor_native_messages WHERE sender_id=? AND submission_id=?",
            (operation.sender_id, submission_id),
        ).fetchone()
        if row is not None:
            if row["principal_json"] != _principal_json(sender):
                raise ValueError("Canonical message sender conflicts with bound operation")
            if row["recipient_json"] != _principal_json(recipient):
                raise ValueError("Canonical message recipient conflicts with bound operation")
    return tuple(
        row
        for row in connection.execute(
            "SELECT * FROM executor_native_messages WHERE submission_id=? "
            "AND principal_json=? AND recipient_json=?",
            (submission_id, _principal_json(sender), _principal_json(recipient)),
        )
        if row["result_json"] is None
        or json.loads(row["result_json"]).get("conversation_id") == conversation_id
    )


def record_native_message_admission_in_transaction(
    connection: sqlite3.Connection,
    conversation_id: str,
    message: PromptDiscardedEventPayload,
    *,
    queued: bool = False,
) -> None:
    """Keep only the operation's outcome knowledge, never a recoverable payload.

    The shared dispatch decision calls this before any provider can see the text.
    A proven non-delivery can return it to queued. The first receipt is unchanged.
    """
    for row in _message_operations(
        connection, conversation_id, message.sender_message_id, message.sender, message.recipient
    ):
        if row["state"] == "completed":
            continue
        result: JsonObject = {
            "target": cast(JsonObject, json.loads(row["recipient_json"])),
            "conversation_id": conversation_id,
            "fate": "queued" if queued else "uncertain",
        }
        connection.execute(
            "UPDATE executor_native_messages SET state=?,result_json=? "
            "WHERE sender_id=? AND submission_id=?",
            (
                "bound" if queued else "dispatching",
                _json(result),
                row["sender_id"],
                row["submission_id"],
            ),
        )


def interrupt_native_messages_in_transaction(connection: sqlite3.Connection) -> None:
    """A runtime restart loses waiting text; an attempted dispatch stays unknown."""
    for row in connection.execute(
        "SELECT * FROM executor_native_messages WHERE state IN ('bound','dispatching')"
    ).fetchall():
        result: JsonObject = {
            "target": cast(JsonObject, json.loads(row["recipient_json"])),
            "conversation_id": (
                json.loads(row["result_json"]).get("conversation_id")
                if row["result_json"] is not None
                else None
            ),
            "fate": "interrupted" if row["state"] == "bound" else "uncertain",
        }
        _save_result(connection, row, result)


def record_native_message_event_in_transaction(
    connection: sqlite3.Connection,
    conversation_id: str,
    payload: ConversationEventPayload,
    *,
    native_message_operation: NativeMessageOperation | None = None,
    native_message_admission: NativeMessageAdmission | None = None,
) -> None:
    """Settle immediate or held delivery with its canonical event and reply credit."""
    admission = native_message_admission
    composed = False
    if isinstance(payload, MessageToOwnerEventPayload):
        fate: JsonObject = {"fate": "recorded"}
        composed = True
    elif isinstance(payload, PromptEventPayload):
        if admission is None or not isinstance(
            admission.fate, (PromptDeliveryStarted, PromptDeliveryInjected)
        ):
            return
        fate = cast(JsonObject, delivery_fate_json(admission.fate))
        composed = admission.composed_content_delivered
    elif isinstance(payload, PromptDeliveryUncertainEventPayload):
        fate = {"fate": "uncertain"}
        composed = admission.composed_content_delivered if admission is not None else False
    elif isinstance(payload, PromptDeliveryRefusedEventPayload):
        fate = {"fate": "refused", "refusal_reason": payload.refusal_reason.value}
    elif isinstance(payload, PromptDiscardedEventPayload):
        fate = {"fate": "discarded"}
    else:
        return
    for row in _message_operations(
        connection,
        conversation_id,
        payload.sender_message_id,
        payload.sender,
        payload.recipient,
        native_message_operation=native_message_operation,
    ):
        result: JsonObject = {
            "target": cast(JsonObject, json.loads(row["recipient_json"])),
            "conversation_id": conversation_id,
            **fate,
        }
        _save_result(connection, row, result, composed_content_delivered=composed)
        operation = native_message_operation
        if (
            composed
            and operation is not None
            and (row["sender_id"], row["submission_id"])
            == (operation.sender_id, operation.submission_id)
        ):
            operation.composed_confirmed = True


async def deliver_native_message(
    *,
    store: ExecutorStore,
    envelope: QueuedEnvelope,
    write: CliWrite,
    principal: Principal,
    identity: ExecutorIdentity,
    clock: Clock,
    conversations: ConversationSystem,
    resolve_recipient: RecipientResolver,
) -> AcceptanceReceipt:
    """Called under the endpoint's per-conversation lock through final receipt.

    Resolver checks original help identity/target and chain only on first admission.
    Replays read the original canonical outcome before any mutable reachability.
    """
    prior = await settled_io(partial(store.preflight, envelope, identity=identity))
    if prior is not None:
        return prior

    def admit() -> tuple[bool, JsonObject, Principal, str, MessageDeliveryMode]:
        with store._transaction() as connection:
            store._preflight(connection, envelope, identity)
            row = _lookup(connection, envelope)
            if row is not None:
                if row["principal_json"] != _principal_json(principal):
                    raise PlannerError(ErrorCode.validation, "Native sender binding conflicts", {})
                if row["result_json"] is not None:
                    result = cast(JsonObject, json.loads(row["result_json"]))
                    dispatch = False
                elif row["state"] == "dispatching":
                    result = {
                        "target": cast(JsonObject, json.loads(row["recipient_json"])),
                        "conversation_id": None,
                        "fate": "uncertain",
                    }
                    _save_result(connection, row, result)
                    dispatch = False
                else:
                    raise RuntimeError("Native operation has an incomplete admission binding")
                recipient = TypeAdapter(Principal).validate_json(row["recipient_json"])
                message = ""
                mode = api._message_delivery_mode()
            else:
                body = write.arguments.get("body")
                if not isinstance(body, dict):
                    raise PlannerError(ErrorCode.validation, "message body must be an object", {})
                message = api._message_text(body.get("message"))
                if write.operation == "ticket.request_help":
                    if set(body) - {"message", "recipient"}:
                        raise PlannerError(ErrorCode.validation, "unknown help request field", {})
                    mode = api._message_delivery_mode()
                else:
                    mode = (
                        api._message_delivery_mode(body["mode"])
                        if "mode" in body
                        else (api._message_delivery_mode())
                    )
                store.install_files(envelope.file_dependencies)
                recipient = resolve_recipient(connection, write, principal)
                service.require_reach(connection, principal, recipient)
                connection.execute(
                    "INSERT INTO "
                    "executor_native_messages(sender_id,submission_id,payload_sha256,"
                    "principal_json,recipient_json,source_turn_json,state,result_json,"
                    "created_at_ms) "
                    "VALUES (?,?,?,?,?,?,'dispatching',NULL,?)",
                    (
                        envelope.sender_id,
                        envelope.submission_id,
                        envelope.payload_sha256,
                        _principal_json(principal),
                        _principal_json(recipient),
                        _json(asdict(write.turn)) if write.turn else None,
                        _now_ms(),
                    ),
                )
                dispatch = True
                result = {}
        return dispatch, result, recipient, message, mode

    try:
        dispatch, result, recipient, message, mode = await settled_io(admit)
    except PlannerError as error:
        rejection = error

        def refuse(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
            raise rejection

        return await settled_io(
            partial(store.accept, envelope, refuse, _now_ms(), identity=identity)
        )

    if dispatch:
        from planner.core.db import connect

        connection = connect(store.db_path)
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
                native_message_operation=NativeMessageOperation(
                    envelope.sender_id, envelope.submission_id
                ),
            )
            result = cast(JsonObject, api._result_json(delivered))
        except PlannerError as error:
            result = cast(JsonObject, error.to_payload())
        finally:
            connection.close()

        # The event hook has already saved an actual native/owner outcome. Queued
        # or immediate-refusal outcomes have no event, so save their actual return.
        def save_returned_result() -> JsonObject:
            with store._transaction() as connection:
                store._connected(connection, identity)
                row = _lookup(connection, envelope)
                assert row is not None
                if row["result_json"] is None or result.get("fate") != "queued":
                    _save_result(connection, row, result)
                saved = connection.execute(
                    "SELECT result_json FROM executor_native_messages "
                    "WHERE sender_id=? AND submission_id=?",
                    (envelope.sender_id, envelope.submission_id),
                ).fetchone()
                return cast(JsonObject, json.loads(saved[0]))

        result = await settled_io(save_returned_result)

    def accepted(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        del connection, envelope
        error = result.get("error")
        if isinstance(error, dict):
            raise PlannerError(
                ErrorCode(str(error["code"])),
                str(error["message"]),
                cast(dict[str, object], error.get("detail", {})),
            )
        return result

    return await settled_io(partial(store.accept, envelope, accepted, _now_ms(), identity=identity))
