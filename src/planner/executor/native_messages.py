"""Bind one incoming native message to its original target and canonical outcome.

The incoming operation record lives in the authoritative VPS database. It is not
an outbound queue. A dispatched operation with no canonical outcome is uncertain;
recovery never sends it to another holder or native child.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import cast

from pydantic import TypeAdapter

from planner.conversation.api import delivery_fate_json
from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryFate,
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
from planner.message_delivery import api, service


# The context reaches ConversationStore's shielded asyncio.to_thread append. The
# sender ID check prevents a background queued promotion from crediting another op.
@dataclass(slots=True)
class _Operation:
    sender_id: str
    submission_id: str
    envelope: QueuedEnvelope
    store: ExecutorStore
    identity: ExecutorIdentity
    composed_confirmed: bool = False


_OPERATION: ContextVar[_Operation | None] = ContextVar("executor_native_message", default=None)
type RecipientResolver = Callable[[sqlite3.Connection, CliWrite, Principal], Principal]


@dataclass(frozen=True, slots=True)
class _Admission:
    fate: PromptDeliveryFate
    composed_content_delivered: bool


_ADMISSION: ContextVar[_Admission | None] = ContextVar("executor_native_admission", default=None)


@contextmanager
def native_message_admission(
    fate: PromptDeliveryFate, *, composed_content_delivered: bool
) -> Iterator[None]:
    """Core supplies the actual native result at its canonical append boundary."""
    token = _ADMISSION.set(_Admission(fate, composed_content_delivered))
    try:
        yield
    finally:
        _ADMISSION.reset(token)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _principal_json(principal: Principal) -> str:
    return _json({"kind": principal.kind.value, "id": principal.id})


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


@contextmanager
def _operation_context(
    envelope: QueuedEnvelope, store: ExecutorStore, identity: ExecutorIdentity
) -> Iterator[None]:
    token = _OPERATION.set(
        _Operation(envelope.sender_id, envelope.submission_id, envelope, store, identity)
    )
    try:
        yield
    finally:
        _OPERATION.reset(token)


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
    if row["result_json"] is not None:
        return
    connection.execute(
        "UPDATE executor_native_messages SET result_json=?,state='completed' "
        "WHERE sender_id=? AND submission_id=? AND result_json IS NULL",
        (_json(result), row["sender_id"], row["submission_id"]),
    )


def native_reply_was_composed() -> bool | None:
    operation = _OPERATION.get()
    return None if operation is None else operation.composed_confirmed


def record_native_held_prompt(
    conversation_id: str, held_prompt: object, queue_position: int
) -> None:
    """Publish one actual held admission and its first receipt in one VPS transaction."""
    operation = _OPERATION.get()
    if operation is None:
        return
    from planner.executor.codec import encode_value

    held = encode_value(held_prompt)
    if not isinstance(held, dict) or held.get("sender_message_id") != operation.submission_id:
        raise ValueError("held message does not match native submission")

    def write(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        row = _lookup(connection, envelope)
        if row is None:
            raise ValueError("held message lacks its original target binding")
        if held.get("sender") != json.loads(row["principal_json"]) or held.get(
            "recipient"
        ) != json.loads(row["recipient_json"]):
            raise ValueError("held identity differs from original operation")
        if row["held_prompt_json"] is not None:
            if (
                row["held_prompt_json"] != _json(held)
                or row["held_target_conversation_id"] != conversation_id
            ):
                raise ValueError("native operation already holds another message")
        else:
            order = connection.execute(
                "SELECT COALESCE(MAX(held_admission_order),0)+1 FROM "
                "executor_native_messages WHERE held_target_conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE executor_native_messages SET "
                "held_prompt_json=?,held_target_conversation_id=?,held_admission_order=?,"
                "held_state='pending' "
                "WHERE sender_id=? AND submission_id=?",
                (_json(held), conversation_id, order, row["sender_id"], row["submission_id"]),
            )
        result: JsonObject = {
            "target": cast(JsonObject, json.loads(row["recipient_json"])),
            "conversation_id": conversation_id,
            "fate": "queued",
            "queue_position": queue_position,
        }
        _save_result(connection, row, result)
        return result

    operation.store.accept(operation.envelope, write, _now_ms(), identity=operation.identity)


def consume_native_held_prompt_in_transaction(
    connection: sqlite3.Connection, conversation_id: str, payload: ConversationEventPayload
) -> None:
    """One terminal canonical sender outcome consumes its exact held admission."""
    if not isinstance(
        payload,
        (
            PromptEventPayload,
            PromptDeliveryRefusedEventPayload,
            PromptDeliveryUncertainEventPayload,
            PromptDiscardedEventPayload,
        ),
    ):
        return
    submission_id = payload.sender_message_id
    if submission_id is None:
        return
    rows = connection.execute(
        "SELECT * FROM executor_native_messages WHERE submission_id=? AND "
        "held_target_conversation_id=? AND held_state IN "
        "('pending','dispatching')",
        (submission_id, conversation_id),
    ).fetchall()
    for row in rows:
        if payload.sender != TypeAdapter(Principal).validate_json(
            row["principal_json"]
        ) or payload.recipient != TypeAdapter(Principal).validate_json(row["recipient_json"]):
            continue
        held = json.loads(row["held_prompt_json"])
        from planner.executor.codec import encode_value

        if (
            held["content"] != encode_value(payload.content)
            or held["sender_label"] != payload.sender_label
        ):
            raise ValueError("terminal sender event differs from canonical held bytes")
        if isinstance(payload, PromptDeliveryUncertainEventPayload):
            # Keep the original dispatch bytes for reconciliation, without reply
            # credit or authority to emit them again.
            continue
        admission = _ADMISSION.get()
        if (
            isinstance(payload, (PromptEventPayload, PromptDeliveryUncertainEventPayload))
            and admission is not None
            and admission.composed_content_delivered
        ):
            _credit_reply(connection, row, {"fate": "started"})
        connection.execute(
            "UPDATE executor_native_messages SET held_state='consumed' WHERE "
            "sender_id=? AND submission_id=?",
            (row["sender_id"], row["submission_id"]),
        )


def record_native_message_event_in_transaction(
    connection: sqlite3.Connection, conversation_id: str, payload: ConversationEventPayload
) -> None:
    """Hook after canonical row insert and before its commit, never after receipt."""
    operation = _OPERATION.get()
    if operation is None or getattr(payload, "sender_message_id", None) != operation.submission_id:
        return
    admission = _ADMISSION.get()
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
    else:
        return
    row = connection.execute(
        "SELECT * FROM executor_native_messages WHERE sender_id=? AND submission_id=?",
        (operation.sender_id, operation.submission_id),
    ).fetchone()
    if row is None:
        return
    if getattr(payload, "sender", None) != TypeAdapter(Principal).validate_json(
        row["principal_json"]
    ):
        raise ValueError("Canonical message sender conflicts with bound operation")
    if getattr(payload, "recipient", None) != TypeAdapter(Principal).validate_json(
        row["recipient_json"]
    ):
        raise ValueError("Canonical message recipient conflicts with bound operation")
    result: JsonObject = {
        "target": cast(JsonObject, json.loads(row["recipient_json"])),
        "conversation_id": conversation_id,
        **fate,
    }
    _save_result(connection, row, result, composed_content_delivered=composed)
    if composed:
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
    prior = store.preflight(envelope, identity=identity)
    if prior is not None:
        return prior
    try:
        with store._transaction() as connection:
            store._preflight(connection, envelope, identity)
            row = _lookup(connection, envelope)
            if row is not None:
                if row["principal_json"] != _principal_json(principal):
                    raise PlannerError(ErrorCode.validation, "Native sender binding conflicts", {})
                if row["result_json"] is not None:
                    result = cast(JsonObject, json.loads(row["result_json"]))
                    dispatch = False
                elif row["state"] == "dispatched":
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
                recipient = resolve_recipient(connection, write, principal)
                service.require_reach(connection, principal, recipient)
                connection.execute(
                    "INSERT INTO "
                    "executor_native_messages(sender_id,submission_id,payload_sha256,"
                    "principal_json,recipient_json,source_turn_json,state,result_json,"
                    "created_at_ms) "
                    "VALUES (?,?,?,?,?,?,'dispatched',NULL,?)",
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
    except PlannerError as error:
        rejection = error

        def refuse(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
            raise rejection

        return store.accept(envelope, refuse, _now_ms(), identity=identity)

    if dispatch:
        from planner.core.db import connect

        connection = connect(store.db_path)
        try:
            with _operation_context(envelope, store, identity):
                delivered = await service.send_message(
                    conversations,
                    connection,
                    clock,
                    RequestContext(principal),
                    recipient,
                    message,
                    mode,
                    sender_message_id=envelope.submission_id,
                )
            result = cast(JsonObject, api._result_json(delivered))
        except PlannerError as error:
            result = cast(JsonObject, error.to_payload())
        finally:
            connection.close()
        # The event hook has already saved an actual native/owner outcome. Queued
        # or immediate-refusal outcomes have no event, so save their actual return.
        with store._transaction() as connection:
            store._connected(connection, identity)
            row = _lookup(connection, envelope)
            assert row is not None
            _save_result(connection, row, result)
            saved = connection.execute(
                "SELECT result_json FROM executor_native_messages "
                "WHERE sender_id=? AND submission_id=?",
                (envelope.sender_id, envelope.submission_id),
            ).fetchone()
            result = cast(JsonObject, json.loads(saved[0]))

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

    return store.accept(envelope, accepted, _now_ms(), identity=identity)
