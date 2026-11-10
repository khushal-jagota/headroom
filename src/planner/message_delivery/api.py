"""HTTP entry point for the general Panels Send Message command."""

from __future__ import annotations

from fastapi import APIRouter, Request

from planner.conversation.api import delivery_fate_json
from planner.core.authctx import require_owner
from planner.core.contracts import JsonDict, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery import recap_request, service
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageRecordedToOwner,
)
from planner.tickets.api import Clk, Conversations, Ctx, connection_off_the_loop

router = APIRouter()

_MISSING = object()


def _principal(raw: object) -> Principal:
    if not isinstance(raw, dict):
        raise PlannerError(ErrorCode.validation, "target must be a principal object", {})
    raw_kind = raw.get("kind")
    if not isinstance(raw_kind, str):
        raise PlannerError(
            ErrorCode.validation,
            "recipient kind must be owner, chief, manager, or ticket",
            {"kind": raw_kind},
        )
    try:
        kind = PrincipalKind(raw_kind)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation,
            "recipient kind must be owner, chief, manager, or ticket",
            {"kind": raw_kind},
        ) from exc
    if kind not in (
        PrincipalKind.owner,
        PrincipalKind.chief,
        PrincipalKind.manager,
        PrincipalKind.ticket,
    ):
        raise PlannerError(
            ErrorCode.validation, "unknown message recipient kind", {"kind": raw_kind}
        )
    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise PlannerError(
            ErrorCode.validation,
            f"{kind.value} recipient id is required",
            {"id": raw_id},
        )
    try:
        return Principal(kind, raw_id.strip())
    except ValueError as exc:
        raise PlannerError(ErrorCode.validation, str(exc), {"id": raw_id}) from exc


def _message_text(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise PlannerError(ErrorCode.validation, "message must not be empty", {})
    return raw


def _message_delivery_mode(raw: object = _MISSING) -> MessageDeliveryMode:
    if raw is _MISSING:
        return MessageDeliveryMode.steer
    if isinstance(raw, str):
        try:
            return MessageDeliveryMode(raw)
        except ValueError:
            pass
    raise PlannerError(
        ErrorCode.validation,
        "mode must be queue, steer, or send_now",
        {"mode": raw},
    )


def _result_json(result: MessageDeliveryResult) -> JsonDict:
    fate = (
        {"fate": "recorded"}
        if isinstance(result.fate, MessageRecordedToOwner)
        else delivery_fate_json(result.fate)
    )
    return {
        "target": {
            "kind": result.recipient.kind.value,
            "id": result.recipient.id,
        },
        "conversation_id": result.conversation_id,
        **fate,
    }


@router.post("/messages/send")
async def send_message(
    body: JsonDict,
    request: Request,
    ctx: Ctx,
    clock: Clk,
    conversations: Conversations,
) -> JsonDict:
    recipient = _principal(body.get("target"))
    message = _message_text(body.get("message"))
    mode = _message_delivery_mode(body.get("mode", _MISSING))
    async with connection_off_the_loop(request) as conn:
        result = await service.send_message(
            conversations, conn, clock, ctx, recipient, message, mode
        )
    return _result_json(result)


@router.post("/recap-requests")
async def request_recap(
    body: JsonDict,
    request: Request,
    ctx: Ctx,
    clock: Clk,
    conversations: Conversations,
) -> JsonDict:
    """Ask one Ticket or manager for a fresh recap.

    Only the owner asks. An agent that wants another agent's recap is sending an ordinary
    message, and Send Message is already that door.
    """
    require_owner(ctx)
    recipient = _principal(body.get("target"))
    async with connection_off_the_loop(request) as conn:
        result = await recap_request.request_recap(conversations, conn, clock, ctx, recipient)
    return _result_json(result)
