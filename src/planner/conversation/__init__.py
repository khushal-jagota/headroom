"""The conversation contract: how the rest of Panels talks to the conversation system."""

from typing import TYPE_CHECKING, cast

from planner.conversation.contracts import (
    BACKEND_KEYS_SUPPORTING_STEER,
    FLOOR_DEFAULT_ACCESS,
    FLOOR_DEFAULT_BACKEND_KEY,
    FLOOR_DEFAULT_WORKSPACE_FOLDER,
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    ConversationSystem,
    HeldPrompt,
    HeldPromptPromotionFate,
    HeldPromptPromotionMode,
    PromptDeliveryFate,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliverySuppressed,
    PromptDeliveryUncertain,
    PromptQueueReason,
    ResolvedConversationStart,
    backend_supports_steer,
)
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)

if TYPE_CHECKING:
    from planner.conversation.in_memory_conversation_system import (
        InMemoryBackendPromptWrite,
        InMemoryConversationObservation,
        InMemoryConversationObservationKind,
        InMemoryConversationSystem,
        InMemoryConversationTurnEnding,
        TurnCannotEndWhilePermissionAskIsPending,
        TurnCannotEndWhileUserInputIsPending,
    )


def __getattr__(name: str) -> object:
    # Contract imports must not pull the implementation back through executor types.
    if name in {
        "InMemoryBackendPromptWrite",
        "InMemoryConversationObservation",
        "InMemoryConversationObservationKind",
        "InMemoryConversationSystem",
        "InMemoryConversationTurnEnding",
        "TurnCannotEndWhilePermissionAskIsPending",
        "TurnCannotEndWhileUserInputIsPending",
    }:
        from planner.conversation import in_memory_conversation_system

        return cast(object, getattr(in_memory_conversation_system, name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BACKEND_KEYS_SUPPORTING_STEER",
    "FLOOR_DEFAULT_ACCESS",
    "FLOOR_DEFAULT_BACKEND_KEY",
    "FLOOR_DEFAULT_WORKSPACE_FOLDER",
    "ConversationAccess",
    "ConversationAlreadyStarted",
    "ConversationBackendKey",
    "ConversationRoleMaterials",
    "ConversationStartRequest",
    "ConversationSystem",
    "HeldPrompt",
    "HeldPromptPromotionFate",
    "HeldPromptPromotionMode",
    "InMemoryBackendPromptWrite",
    "InMemoryConversationObservation",
    "InMemoryConversationObservationKind",
    "InMemoryConversationSystem",
    "InMemoryConversationTurnEnding",
    "PromptDeliveryFate",
    "PromptDeliveryInjected",
    "PromptDeliveryMode",
    "PromptQueueReason",
    "PromptDeliveryQueued",
    "PromptDeliveryRefusalReason",
    "PromptDeliveryRefused",
    "PromptDeliverySuppressed",
    "PromptDeliveryStarted",
    "PromptDeliveryUncertain",
    "ResolvedConversationStart",
    "TurnCannotEndWhilePermissionAskIsPending",
    "TurnCannotEndWhileUserInputIsPending",
    "backend_supports_steer",
    "resolve_conversation_start_request",
]
