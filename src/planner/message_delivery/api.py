"""HTTP entry point for the general Panels Send Message command."""

from __future__ import annotations

from fastapi import APIRouter

from planner.conversation.api import delivery_fate_json
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery import service
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageTarget,
    MessageTargetType,
)
from planner.tickets.api import Clk, Conversations, Ctx, DbConn

router = APIRouter()

_MISSING = object()


def _message_target(raw: object) -> MessageTarget:
    if not isinstance(raw, dict):
        raise PlannerError(ErrorCode.validation, "target must be an object", {})
    raw_type = raw.get("type")
    if not isinstance(raw_type, str):
        raise PlannerError(
            ErrorCode.validation,
            "target type must be chief, ticket, sprint_item, or agent",
            {"type": raw_type},
        )
    try:
        target_type = MessageTargetType(raw_type)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation,
            "target type must be chief, ticket, sprint_item, or agent",
            {"type": raw_type},
        ) from exc
    raw_id = raw.get("id")
    if target_type is MessageTargetType.chief:
        if raw_id is not None:
            raise PlannerError(
                ErrorCode.validation,
                "a chief target does not take an id",
                {"id": raw_id},
            )
        return MessageTarget(target_type)
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise PlannerError(
            ErrorCode.validation,
            f"{target_type.value} target id is required",
            {"id": raw_id},
        )
    return MessageTarget(target_type, raw_id.strip())


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
        "mode must be queue, steer, or send_now",
        {"mode": raw},
    )


def _result_json(result: MessageDeliveryResult) -> JsonDict:
    target: JsonDict = {"type": result.target.target_type.value}
    if result.target.target_id is not None:
        target["id"] = result.target.target_id
    fate = delivery_fate_json(result.fate)
    return {
        "target": target,
        "resolved_destination": {
            "type": result.resolved_destination.destination_type,
            "id": result.resolved_destination.destination_id,
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
    target = _message_target(body.get("target"))
    message = _message_text(body.get("message"))
    mode = _message_delivery_mode(body.get("mode", _MISSING))
    result = await service.send_message(conversations, conn, clock, ctx, target, message, mode)
    return _result_json(result)
