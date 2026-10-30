"""Existing conversation types crossing the executor boundary, validated once."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import (
    AgentMessageCompletedArguments,
    AgentMessageDeltaArguments,
    BackendChild,
    BackendPromptAccepted,
    BackendSteerAccepted,
    BackendSteerRefused,
    ComposerCatalogReportedArguments,
    ContextCompactedArguments,
    DecodedBackendEventArguments,
    ModelThinkingHappenedArguments,
    PermissionAskRaisedArguments,
    PlanUpdatedArguments,
    ProviderSelectionReportedArguments,
    TokenUsageReportedArguments,
    ToolCallFinishedArguments,
    ToolCallProgressArguments,
    ToolCallStartedArguments,
    TurnEndedArguments,
    UserInputFailedArguments,
    UserInputRequestedArguments,
    VendorSessionCursorReboundArguments,
)
from planner.conversation.contracts import (
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ProviderSubmissionFact,
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
                "provider_submission": encode_value(result.provider_submission),
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
                return {
                    "kind": "steer_refused",
                    "refusal_reason": outcome.refusal_reason.value,
                    "provider_submission": encode_value(outcome.provider_submission),
                }
            return {
                "kind": "steer_accepted"
                if isinstance(outcome, BackendSteerAccepted)
                else "steer_uncertain",
                "composed_content_delivered": outcome.composed_content_delivered,
                "provider_submission": encode_value(outcome.provider_submission)
                if isinstance(outcome, BackendSteerAccepted)
                else None,
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


def decode_event_arguments(event: BackendEvent) -> DecodedBackendEventArguments:
    """Decode one closed callback shape without changing its wire representation."""
    match event.method:
        case BackendEventMethod.agent_message_delta:
            return TypeAdapter(AgentMessageDeltaArguments).validate_python(event.arguments)
        case BackendEventMethod.model_thinking_happened:
            return TypeAdapter(ModelThinkingHappenedArguments).validate_python(event.arguments)
        case BackendEventMethod.provider_selection_reported:
            return TypeAdapter(ProviderSelectionReportedArguments).validate_python(event.arguments)
        case BackendEventMethod.agent_message_completed:
            return TypeAdapter(AgentMessageCompletedArguments).validate_python(event.arguments)
        case BackendEventMethod.tool_call_started:
            return TypeAdapter(ToolCallStartedArguments).validate_python(event.arguments)
        case BackendEventMethod.tool_call_progress:
            return TypeAdapter(ToolCallProgressArguments).validate_python(event.arguments)
        case BackendEventMethod.tool_call_finished:
            return TypeAdapter(ToolCallFinishedArguments).validate_python(event.arguments)
        case BackendEventMethod.plan_updated:
            return TypeAdapter(PlanUpdatedArguments).validate_python(event.arguments)
        case BackendEventMethod.token_usage_reported:
            return TypeAdapter(TokenUsageReportedArguments).validate_python(event.arguments)
        case BackendEventMethod.context_compacted:
            return TypeAdapter(ContextCompactedArguments).validate_python(event.arguments)
        case BackendEventMethod.permission_ask_raised:
            return TypeAdapter(PermissionAskRaisedArguments).validate_python(event.arguments)
        case BackendEventMethod.user_input_requested:
            return TypeAdapter(UserInputRequestedArguments).validate_python(event.arguments)
        case BackendEventMethod.user_input_failed:
            return TypeAdapter(UserInputFailedArguments).validate_python(event.arguments)
        case BackendEventMethod.turn_ended:
            return TypeAdapter(TurnEndedArguments).validate_python(event.arguments)
        case BackendEventMethod.vendor_session_cursor_rebound:
            return TypeAdapter(VendorSessionCursorReboundArguments).validate_python(event.arguments)
        case BackendEventMethod.composer_catalog_reported:
            return TypeAdapter(ComposerCatalogReportedArguments).validate_python(event.arguments)


def decode_prompt_result(result: JsonObject) -> BackendPromptAccepted:
    return BackendPromptAccepted(
        composed_content_delivered=bool(result["composed_content_delivered"]),
        provider_submission=decode_provider_submission(result.get("provider_submission")),
    )


def decode_provider_submission(raw: object) -> ProviderSubmissionFact | None:
    """Historical outcomes remain readable without an invented provider fact."""
    if raw is None:
        return None
    return TypeAdapter(ProviderSubmissionFact).validate_python(raw)
