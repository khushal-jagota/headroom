"""Existing conversation types crossing the executor boundary, validated once."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    BackendSteerAccepted,
    BackendSteerRefused,
    BackendUserInputRequest,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    PlanEntry,
    ToolCallStatus,
    UserInputAnswer,
)
from planner.conversation.message_content import MessageContent
from planner.executor.contracts import (
    BackendEvent,
    BackendEventMethod,
    ChildCommand,
    ChildOperation,
    JsonObject,
    JsonValue,
)


def encode_value(value: object) -> JsonValue:
    return TypeAdapter(object).dump_python(value, mode="json")  # type: ignore[no-any-return]


def encode_backend_event(
    method: BackendEventMethod,
    turn: object,
    arguments: dict[str, object],
) -> JsonObject:
    del method, turn
    return {key: encode_value(value) for key, value in arguments.items()}


async def invoke_child(child: BackendChild, command: ChildCommand) -> JsonObject:
    """Invoke one already deduplicated native operation; never retry it here."""
    args: dict[str, Any] = dict(command.arguments)
    match command.operation:
        case ChildOperation.start:
            await child.start(
                TypeAdapter(ResolvedConversationStart).validate_python(args["resolved_start"]),
                vendor_session_cursor=args.get("vendor_session_cursor"),
            )
        case ChildOperation.write_prompt:
            if command.turn is None:
                raise ValueError("write_prompt needs its exact turn")
            result = await child.write_prompt(
                command.turn,
                TypeAdapter(MessageContent).validate_python(args["content"]),
                sender_label=args["sender_label"],
                sender_content=TypeAdapter(MessageContent).validate_python(args["sender_content"]),
                sender_message_count=args.get("sender_message_count", 1),
                mode=PromptDeliveryMode(args["mode"]),
                model_change=args.get("model_change"),
                reasoning_effort_change=args.get("reasoning_effort_change"),
                automatic_compaction=args.get("automatic_compaction", False),
            )
            return {
                "kind": "prompt_accepted",
                "composed_content_delivered": result.composed_content_delivered,
            }
        case ChildOperation.steer:
            if command.turn is None:
                raise ValueError("steer needs its exact turn")
            outcome = await child.steer(
                command.turn,
                TypeAdapter(MessageContent).validate_python(args["content"]),
                sender_content=(
                    None
                    if args.get("sender_content") is None
                    else TypeAdapter(MessageContent).validate_python(args["sender_content"])
                ),
                sender_label=args["sender_label"],
            )
            if isinstance(outcome, BackendSteerRefused):
                return {"kind": "steer_refused", "refusal_reason": outcome.refusal_reason.value}
            return {
                "kind": "steer_accepted"
                if isinstance(outcome, BackendSteerAccepted)
                else "steer_uncertain",
                "composed_content_delivered": outcome.composed_content_delivered,
            }
        case ChildOperation.cancel_running_turn:
            await child.cancel_running_turn()
        case ChildOperation.answer_permission_ask:
            await child.answer_permission_ask(args["ask_id"], args["option_id"])
        case ChildOperation.answer_user_input:
            await child.answer_user_input(
                args["request_id"],
                TypeAdapter(tuple[UserInputAnswer, ...]).validate_python(args["answers"]),
            )
        case ChildOperation.stop:
            await child.stop()
    return {}


def decode_event_arguments(event: BackendEvent) -> dict[str, Any]:
    args: dict[str, Any] = dict(event.arguments)
    adapters: dict[BackendEventMethod, dict[str, object]] = {
        BackendEventMethod.agent_message_completed: {"content": MessageContent},
        BackendEventMethod.permission_ask_raised: {"ask": BackendPermissionAsk},
        BackendEventMethod.user_input_requested: {"request": BackendUserInputRequest},
        BackendEventMethod.plan_updated: {"entries": tuple[PlanEntry, ...]},
        BackendEventMethod.tool_call_finished: {"tool_call_status": ToolCallStatus},
        BackendEventMethod.turn_ended: {"ending": ConversationTurnEnding},
        BackendEventMethod.composer_catalog_reported: {
            "composer_catalog": tuple[ComposerCatalogEntry, ...]
        },
    }
    for name, target in adapters.get(event.method, {}).items():
        args[name] = TypeAdapter(target).validate_python(args[name])
    return args


async def dispatch_backend_event(sink: BackendEventSink, event: BackendEvent) -> None:
    args = decode_event_arguments(event)
    method = getattr(sink, event.method.value)
    if event.turn is None:
        await method(**args)
    else:
        await method(event.turn, **args)


def decode_prompt_result(result: JsonObject) -> BackendPromptAccepted:
    return BackendPromptAccepted(
        composed_content_delivered=bool(result["composed_content_delivered"])
    )
