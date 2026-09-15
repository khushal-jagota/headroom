"""HTTP entry point for the general Panels Send Message command."""

from __future__ import annotations

from fastapi import APIRouter

from planner.conversation.api import delivery_fate_json
from planner.core.contracts import JsonDict, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery import service
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageRecordedToOwner,
)
from planner.tickets.api import Clk, Conversations, Ctx, DbConn

router = APIRouter()

_MISSING = object()


def _principal(raw: object) -> Principal:
    if not isinstance(raw, dict):
        raise PlannerError(ErrorCode.validation, "target must be a principal object", {})
    raw_kind = raw.get("kind")
    if not isinstance(raw_kind, str):
        raise PlannerError(
            ErrorCode.validation,
            "recipient kind must be owner, chief, sprint_item, or ticket",
            {"kind": raw_kind},
        )
    try:
        kind = PrincipalKind(raw_kind)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation,
            "recipient kind must be owner, chief, sprint_item, or ticket",
            {"kind": raw_kind},
        ) from exc
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
        return MessageDeliveryMode.queue
    if isinstance(raw, str):
        try:
            return MessageDeliveryMode(raw)
        except ValueError:
            pass
    raise PlannerError(
        ErrorCode.validation,
        "mode must be queue or steer",
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
    conn: DbConn,
    ctx: Ctx,
    clock: Clk,
    conversations: Conversations,
) -> JsonDict:
    recipient = _principal(body.get("target"))
    message = _message_text(body.get("message"))
    mode = _message_delivery_mode(body.get("mode", _MISSING))
    result = await service.send_message(conversations, conn, clock, ctx, recipient, message, mode)
    return _result_json(result)
