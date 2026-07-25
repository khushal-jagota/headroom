"""Applying the floor defaults to a conversation start request."""

from __future__ import annotations

from planner.conversation2.contracts import (
    FLOOR_DEFAULT_ACCESS,
    FLOOR_DEFAULT_BACKEND_KEY,
    FLOOR_DEFAULT_WORKSPACE_FOLDER,
    ConversationStartRequest,
    ResolvedConversationStart,
)


def resolve_conversation_start_request(
    request: ConversationStartRequest,
) -> ResolvedConversationStart:
    """Turn a start request into the concrete values a conversation is started with.

    The three floor defaults are applied here: an absent backend key becomes codex, an
    absent workspace folder becomes ``~/Coding``, and an absent access posture becomes
    full access. Model, reasoning effort and role materials have no floor default, so
    absent stays absent.

    This lives with the contract rather than in each implementation so that every
    implementation resolves from one source. If each held its own copy, "the system
    holds floor defaults" would quietly become two systems with two sets of defaults.

    It is the pure half of starting a conversation; writing the conversation's record is
    the implementation's half.

    Raises ``ValueError`` if the conversation id is empty or untrimmed, or if the
    workspace folder is not an absolute path.
    """
    conversation_id = request.conversation_id
    if not conversation_id or conversation_id != conversation_id.strip():
        raise ValueError("conversation id must be non-empty and trimmed")

    workspace_folder = request.workspace_folder
    if workspace_folder is None:
        workspace_folder = FLOOR_DEFAULT_WORKSPACE_FOLDER
    if not workspace_folder.is_absolute():
        raise ValueError("workspace folder must be an absolute path")

    return ResolvedConversationStart(
        conversation_id=conversation_id,
        backend_key=(
            FLOOR_DEFAULT_BACKEND_KEY if request.backend_key is None else request.backend_key
        ),
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        role_materials=request.role_materials,
        workspace_folder=workspace_folder,
        access=FLOOR_DEFAULT_ACCESS if request.access is None else request.access,
    )
