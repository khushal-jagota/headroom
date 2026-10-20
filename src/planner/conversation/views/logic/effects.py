"""Classify a source event without storage or framework dependencies."""

from planner.conversation import events as event
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.views.logic.bodies import in_focus
from planner.conversation.views.projection_contracts import EventEffect


def event_effect(payload: event.ConversationEventPayload) -> EventEffect:
    visible = in_focus(payload)
    if isinstance(payload, event.PromptEventPayload):
        return EventEffect(
            "prompt",
            visible,
            opens_turn=payload.mode != PromptDeliveryMode.steer,
            sender_message_id=payload.sender_message_id,
            reconciles_sequence=payload.reconciles_sequence,
        )
    if isinstance(payload, event.PromptDeliveryRefusedEventPayload):
        return EventEffect("prompt_refused", visible, sender_message_id=payload.sender_message_id)
    if isinstance(payload, event.PromptDeliveryUncertainEventPayload):
        return EventEffect("prompt_uncertain", visible, sender_message_id=payload.sender_message_id)
    if isinstance(payload, event.PromptDiscardedEventPayload):
        return EventEffect("prompt_discarded", visible, sender_message_id=payload.sender_message_id)
    if isinstance(payload, (event.AgentMessageEventPayload, event.MessageToOwnerEventPayload)):
        return EventEffect("agent_message", visible)
    if isinstance(payload, event.ExplicitReplyMissingEventPayload):
        return EventEffect("explicit_reply_missing", visible)
    if isinstance(payload, event.ToolCallStartedEventPayload):
        return EventEffect("tool_call", False, "tool", payload.tool_call_id)
    if isinstance(payload, event.ToolCallFinishedEventPayload):
        return EventEffect("tool_call", False, "tool", payload.tool_call_id, settlement=True)
    if isinstance(payload, event.PermissionAskedEventPayload):
        return EventEffect("permission_ask", True, "permission", payload.ask_id)
    if isinstance(payload, event.PermissionAnsweredEventPayload):
        return EventEffect(None, True, "permission", payload.ask_id, settlement=True)
    if isinstance(payload, event.UserInputRequestedEventPayload):
        return EventEffect("user_input", True, "user_input", payload.request_id)
    if isinstance(payload, event.UserInputAnsweredEventPayload):
        return EventEffect(None, True, "user_input", payload.request_id, settlement=True)
    if isinstance(payload, event.UserInputFailedEventPayload):
        return EventEffect("user_input", True, "user_input", payload.request_id, settlement=True)
    if isinstance(payload, event.PlanUpdatedEventPayload):
        return EventEffect(None, False, changes_plan=True)
    if isinstance(payload, event.ModelChangedEventPayload):
        return EventEffect("model_changed", False)
    if isinstance(payload, event.TokenUsageEventPayload):
        return EventEffect("token_usage", False)
    if isinstance(payload, event.ContextCompactedEventPayload):
        return EventEffect("context_compacted", False)
    if isinstance(payload, event.TurnEndedEventPayload):
        return EventEffect("turn_ended", visible, closes_turn=True)
    raise AssertionError(f"Unclassified event: {payload!r}")
