"""HTTP entry point for the general Panels Send Message command."""

from __future__ import annotations

from fastapi import APIRouter

from planner.conversation.api import delivery_fate_json
from planner.core import authority
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


def _as_target(principal: Principal) -> authority.Target | None:
    """The thing this principal is, when something can stand above it.

    Khushal and the Chief are above everything, so nothing is above them and they are no
    target. A message addressed to either asks no authority question.
    """
    if principal.kind is PrincipalKind.ticket:
        return authority.ticket(principal.id)
    if principal.kind is PrincipalKind.sprint_item:
        return authority.outcome(principal.id)
    return None


def _require_reach(conn: DbConn, caller: Principal, recipient: Principal) -> None:
    """Sending down the chain is acting on the recipient. Sending up is only speaking.

    Two principals in one chain can talk, in both directions. Standing above the
    recipient is acting on it — starting a turn in a conversation that belongs to
    something below you — and that is the rule's own question. Standing below it is not:
    a Worker answering the Outcome that asked it something is speaking, and refusing that
    would make the reply every addressed prompt requires impossible to send.

    Strangers are neither, and they refuse. That is the whole of what this door checks,
    and before this it checked nothing at all.
    """
    target = _as_target(recipient)
    if target is None:
        return
    if authority.is_above(conn, caller, target):
        return
    caller_target = _as_target(caller)
    if caller_target is not None and authority.is_above(conn, recipient, caller_target):
        return
    authority.require_above(conn, caller, target)


@router.post("/messages/send")
async def send_message(
    body: JsonDict,
    conn: DbConn,
    ctx: Ctx,
    clock: Clk,
    conversations: Conversations,
) -> JsonDict:
    recipient = _principal(body.get("target"))
    _require_reach(conn, ctx.principal, recipient)
    message = _message_text(body.get("message"))
    mode = _message_delivery_mode(body.get("mode", _MISSING))
    result = await service.send_message(conversations, conn, clock, ctx, recipient, message, mode)
    return _result_json(result)
