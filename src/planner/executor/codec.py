"""Existing conversation types crossing the executor boundary, validated once."""

from __future__ import annotations

import hashlib
import json

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import (
    AgentMessageCompletedArguments,
    AgentMessageDeltaArguments,
    BackendPromptAccepted,
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
from planner.conversation.events import (
    ProviderSubmissionFact,
)
from planner.executor.contracts import (
    BackendEvent,
    BackendEventMethod,
    EnvelopeKind,
    FileManifest,
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


def envelope_payload_digest(
    payload: JsonObject,
    manifests: tuple[FileManifest, ...],
    *,
    sender_id: str,
    conversation_id: str,
    sequence: int,
    submission_id: str,
    kind: EnvelopeKind,
) -> str:
    value = {
        "payload": payload,
        "sender_id": sender_id,
        "conversation_id": conversation_id,
        "sequence": sequence,
        "submission_id": submission_id,
        "kind": kind.value,
        "file_dependencies": TypeAdapter(tuple[FileManifest, ...]).dump_python(
            manifests, mode="json"
        ),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
