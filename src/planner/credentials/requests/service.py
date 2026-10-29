"""Deliver each committed result once through the actual conversation send boundary."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast

from planner.conversation.contracts import (
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliverySuppressed,
    PromptDeliveryUncertain,
)
from planner.conversation.events import ConversationEventPayload
from planner.conversation.message_content import text_message_content

if TYPE_CHECKING:
    from planner.conversation.runtime_composition import ConversationRuntime
from planner.core.authctx import RequestContext
from planner.core.contracts import PlannerError, Principal, PrincipalKind
from planner.credentials.requests import actions, data
from planner.credentials.requests.contracts import CredentialRequestRowBody, RequestDeliveryStatus
from planner.message_delivery.service import _sender_conversation_link_key
from planner.runtime import conversation_start

# Retain authorized work after the browser connection leaves. Runtime shutdown waits
# its own guarded writes and send tasks. A process loss leaves the durable intent unknown.
_tasks: dict[int, set[asyncio.Task[object]]] = {}


async def shield_operation[T](runtime: ConversationRuntime, operation: Coroutine[Any, Any, T]) -> T:
    tasks = _tasks.setdefault(id(runtime), set())
    task = asyncio.create_task(operation)
    tasks.add(cast(asyncio.Task[object], task))

    def done(completed: asyncio.Task[T]) -> None:
        tasks.discard(cast(asyncio.Task[object], completed))
        if not tasks:
            _tasks.pop(id(runtime), None)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(done)
    return await asyncio.shield(task)


async def finish_operations(runtime: ConversationRuntime) -> None:
    while tasks := tuple(_tasks.get(id(runtime), ())):
        await asyncio.gather(*tasks, return_exceptions=True)


async def dispatch(
    runtime: ConversationRuntime, request_id: str, attempt_id: str | None, *, now: Callable[[], int]
) -> CredentialRequestRowBody:
    if attempt_id is None:
        return await runtime.reads.run(lambda conn: data.read(conn, request_id))
    row, attempt = await runtime.reads.run(
        lambda conn: (
            dict(data.row_for(conn, request_id)),
            dict(
                conn.execute(
                    "SELECT * FROM credential_request_delivery_attempts WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()
            ),
        )
    )
    origin = str(row["conversation_id"])
    status: RequestDeliveryStatus
    reason: str | None = None
    principal = Principal(PrincipalKind(row["requester_kind"]), str(row["requester_id"]))
    try:
        link_key = await runtime.reads.run(
            lambda conn: _sender_conversation_link_key(conn, principal)
        )
    except PlannerError:
        status, reason = "refused", "requesting_conversation_retired"
    else:
        async with conversation_start.conversation_link_lock(link_key):
            try:
                await runtime.reads.run(
                    lambda conn: actions.origin_conversation(
                        conn, RequestContext(principal, origin)
                    )
                )
            except PlannerError:
                status, reason = "refused", "requesting_conversation_retired"
            else:
                try:
                    receipt = await runtime.system.send_with_receipt(
                        origin,
                        text_message_content(str(attempt["prompt"])),
                        sender_label="Panels credential request result",
                        sender_message_id=str(attempt["sender_message_id"]),
                        mode=PromptDeliveryMode.steer,
                        reply_requested=False,
                        record_refusal=True,
                    )
                except Exception:
                    # A missing response is not evidence that no provider received the result.
                    status = "uncertain"
                else:
                    match receipt.fate:
                        case PromptDeliveryStarted():
                            status = "started"
                        case PromptDeliveryInjected():
                            status = "injected"
                        case PromptDeliveryQueued():
                            status = "queued"
                        case PromptDeliveryRefused(refusal_reason=refusal):
                            status = (
                                "uncertain"
                                if refusal == PromptDeliveryRefusalReason.write_to_backend_failed
                                else "refused"
                            )
                            reason = str(refusal)
                        case PromptDeliverySuppressed():
                            status = "canceled"
                        case PromptDeliveryUncertain():
                            status = "uncertain"
                        case _:
                            raise AssertionError("unknown credential result delivery fate")

    def settle(
        conn: sqlite3.Connection,
    ) -> tuple[CredentialRequestRowBody, tuple[ConversationEventPayload, ...]]:
        event = data.settle_attempt(conn, attempt_id, status, reason, now())
        return data.read(conn, request_id), (() if event is None else (event,))

    return await runtime.system.record_events_transaction(origin, settle)
