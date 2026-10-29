"""Resolve a bounded view row from its canonical source and settlement records."""

from __future__ import annotations

from planner.conversation import events as event
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.message_content import (
    MessageContent,
    MessageFile,
    MessageImage,
    MessageText,
)
from planner.conversation.tool_detail import readable_tool_call_detail
from planner.conversation.views.configuration import (
    SENT_AFTER_ROW_LIMIT_MILLISECONDS,
    SENT_BEFORE_ROW_LIMIT_MILLISECONDS,
    TOOL_DETAIL_CHARACTERS,
)
from planner.conversation.views.contracts import (
    AskDeadReason,
    ViewMessagePiece,
    ViewRowBody,
)
from planner.core.contracts import PrincipalKind


def content_pieces(content: MessageContent) -> list[ViewMessagePiece]:
    result: list[ViewMessagePiece] = []
    for piece in content:
        if isinstance(piece, MessageText):
            result.append({"piece": "text", "text": piece.text})
        elif isinstance(piece, MessageImage):
            result.append(
                {
                    "piece": "image",
                    "stored_file_id": piece.stored_file_id,
                    "media_type": piece.media_type,
                    "file_name": piece.file_name,
                }
            )
        elif isinstance(piece, MessageFile):
            result.append(
                {
                    "piece": "file",
                    "stored_file_id": piece.stored_file_id,
                    "media_type": piece.media_type,
                    "file_name": piece.file_name,
                    "byte_count": piece.byte_count,
                }
            )
    return result


def sent_at_milliseconds(payload: event.PromptEventPayload, created_at: int) -> int:
    written = created_at * 1000
    minted = payload.sent_at_unix_milliseconds
    if minted is None:
        return written
    difference = minted - written
    if not -SENT_BEFORE_ROW_LIMIT_MILLISECONDS <= difference <= SENT_AFTER_ROW_LIMIT_MILLISECONDS:
        return written
    return minted


def in_focus(payload: event.ConversationEventPayload, owner_label: str = "owner") -> bool:
    if isinstance(
        payload,
        (
            event.PromptEventPayload,
            event.PromptDeliveryRefusedEventPayload,
            event.PromptDeliveryUncertainEventPayload,
            event.PromptDiscardedEventPayload,
        ),
    ):
        return (payload.sender is not None and payload.sender.kind == PrincipalKind.owner) or (
            payload.sender is None
            and payload.recipient is None
            and payload.sender_label == owner_label
        )
    if isinstance(payload, event.MessageToOwnerEventPayload):
        return payload.recipient.kind == PrincipalKind.owner
    if isinstance(payload, event.TurnEndedEventPayload):
        return payload.ending != event.ConversationTurnEnding.completed
    return isinstance(
        payload,
        (
            event.CredentialRequestEventPayload,
            event.ExplicitReplyMissingEventPayload,
            event.PermissionAskedEventPayload,
            event.PermissionAnsweredEventPayload,
            event.UserInputRequestedEventPayload,
            event.UserInputAnsweredEventPayload,
            event.UserInputFailedEventPayload,
        ),
    )


def row_body(
    source: event.ConversationEventPayload,
    *,
    created_at: int,
    settlement: event.ConversationEventPayload | None = None,
    settlement_sequence: int | None = None,
    dead_reason: AskDeadReason | None = None,
    full_tool_detail: bool = False,
    backend_key: ConversationBackendKey = ConversationBackendKey.hermes,
) -> ViewRowBody | None:
    """No history scan: callers supply at most the row and its indexed settlement."""
    if isinstance(source, event.CredentialRequestEventPayload):
        current = (
            settlement if isinstance(settlement, event.CredentialRequestEventPayload) else source
        )
        return current.request
    if isinstance(source, event.PromptEventPayload):
        return {
            "kind": "prompt",
            "content": content_pieces(source.content),
            "sender_label": source.sender_label,
            "mode": source.mode,
            "sent_at_unix_milliseconds": sent_at_milliseconds(source, created_at),
        }
    if isinstance(source, event.PromptDeliveryRefusedEventPayload):
        return {
            "kind": "prompt_refused",
            "content": content_pieces(source.content),
            "sender_label": source.sender_label,
            "reason": source.refusal_reason,
            "refusal_detail": source.refusal_detail,
        }
    if isinstance(
        source, (event.PromptDeliveryUncertainEventPayload, event.PromptDiscardedEventPayload)
    ):
        return {
            "kind": "prompt_uncertain"
            if isinstance(source, event.PromptDeliveryUncertainEventPayload)
            else "prompt_discarded",
            "content": content_pieces(source.content),
            "sender_label": source.sender_label,
        }
    if isinstance(source, (event.AgentMessageEventPayload, event.MessageToOwnerEventPayload)):
        return {
            "kind": "agent_message",
            "content": content_pieces(source.content),
            "to_owner": isinstance(source, event.MessageToOwnerEventPayload),
        }
    if isinstance(source, event.ExplicitReplyMissingEventPayload):
        return {"kind": "explicit_reply_missing", "prompt_sender": source.prompt_sender}
    if isinstance(source, (event.ToolCallStartedEventPayload, event.ToolCallFinishedEventPayload)):
        start = source if isinstance(source, event.ToolCallStartedEventPayload) else None
        finish = (
            settlement
            if isinstance(settlement, event.ToolCallFinishedEventPayload)
            else (source if isinstance(source, event.ToolCallFinishedEventPayload) else None)
        )
        finished_detail = (
            readable_tool_call_detail(finish.detail, backend_key=backend_key)
            if finish is not None
            else None
        )
        detail = (
            finished_detail if finished_detail is not None else (start.detail if start else None)
        )
        capped = (
            finish is not None
            and detail is not None
            and len(detail) > TOOL_DETAIL_CHARACTERS
            and not full_tool_detail
        )
        return {
            "kind": "tool_call",
            "tool_call_id": source.tool_call_id,
            "title": start.title if start else source.tool_call_id,
            "tool_kind": start.tool_kind if start else "other",
            "detail": detail[:TOOL_DETAIL_CHARACTERS] if capped and detail is not None else detail,
            "started_detail": start.detail if start else None,
            "capped_detail_sequence": settlement_sequence
            if capped and finish is not None
            else None,
            "status": finish.tool_call_status.value if finish else "running",
        }
    if isinstance(source, event.PermissionAskedEventPayload):
        answer = (
            settlement if isinstance(settlement, event.PermissionAnsweredEventPayload) else None
        )
        return {
            "kind": "permission_ask",
            "ask_id": source.ask_id,
            "title": source.title,
            "detail": source.detail,
            "options": list(source.options),
            "state": "answered" if answer else ("dead" if dead_reason else "live"),
            "dead_reason": None if answer else dead_reason,
            "answered_option_label": next(
                (
                    option.label
                    for option in source.options
                    if answer and option.option_id == answer.option_id
                ),
                answer.option_id if answer else None,
            ),
        }
    if isinstance(
        source, (event.UserInputRequestedEventPayload, event.UserInputFailedEventPayload)
    ):
        input_answer = (
            settlement if isinstance(settlement, event.UserInputAnsweredEventPayload) else None
        )
        failure = (
            settlement
            if isinstance(settlement, event.UserInputFailedEventPayload)
            else (source if isinstance(source, event.UserInputFailedEventPayload) else None)
        )
        return {
            "kind": "user_input",
            "request_id": source.request_id,
            "questions": list(source.questions)
            if isinstance(source, event.UserInputRequestedEventPayload)
            else [],
            "state": "answered"
            if input_answer
            else ("failed" if failure else ("dead" if dead_reason else "live")),
            "dead_reason": None if input_answer or failure else dead_reason,
            "answers": list(input_answer.answers) if input_answer else None,
            "failure_detail": failure.detail if failure else None,
        }
    if isinstance(source, event.ModelChangedEventPayload):
        return {
            "kind": "model_changed",
            "model": source.model,
            "reasoning_effort": source.reasoning_effort,
        }
    if isinstance(source, event.TokenUsageEventPayload):
        return {
            "kind": "token_usage",
            "input_tokens": source.input_tokens,
            "output_tokens": source.output_tokens,
            "cached_input_tokens": source.cached_input_tokens,
            "cost_usd": source.cost_usd,
        }
    if isinstance(source, event.ContextCompactedEventPayload):
        return {"kind": "context_compacted"}
    if isinstance(source, event.TurnEndedEventPayload):
        return {
            "kind": "turn_ended",
            "ending": source.ending,
            "error_summary": source.error_summary,
            "automatic_compaction_result": source.automatic_compaction_result,
        }
    return None
