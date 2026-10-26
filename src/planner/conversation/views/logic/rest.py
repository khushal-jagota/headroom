"""Pure compact rest-line facts, without presentation labels."""

from planner.conversation import events as event
from planner.conversation.message_content import message_content_text
from planner.conversation.views.configuration import (
    OWNER_MESSAGE_PREVIEW_CHARACTERS,
    REST_EXCERPT_CHARACTERS,
)


def excerpt(text: str) -> str:
    first = text.split("\n", 1)[0].strip()
    if len(first) <= REST_EXCERPT_CHARACTERS:
        return first
    return first[: REST_EXCERPT_CHARACTERS - 1].rstrip() + "…"


def owner_message_preview(text: str) -> str | None:
    """Keep bounded multiline Markdown, without losing leading-blank-line replies."""
    text = text.strip()
    if not text:
        return None
    if len(text) <= OWNER_MESSAGE_PREVIEW_CHARACTERS:
        return text
    return text[: OWNER_MESSAGE_PREVIEW_CHARACTERS - 1].rstrip() + "…"


def event_has_rest_text(payload: event.ConversationEventPayload) -> bool:
    if isinstance(
        payload,
        (
            event.PromptEventPayload,
            event.AgentMessageEventPayload,
            event.MessageToOwnerEventPayload,
        ),
    ):
        return bool(excerpt(message_content_text(payload.content)))
    if isinstance(payload, event.TurnEndedEventPayload):
        return payload.ending != event.ConversationTurnEnding.completed
    return isinstance(
        payload,
        (
            event.PromptDeliveryRefusedEventPayload,
            event.PromptDeliveryUncertainEventPayload,
            event.PromptDiscardedEventPayload,
            event.ExplicitReplyMissingEventPayload,
            event.ToolCallStartedEventPayload,
            event.ToolCallFinishedEventPayload,
        ),
    )
