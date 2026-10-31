"""Bounded prior-conversation metadata behind the existing picker."""

from typing import Literal, TypedDict

HistoryOwnerKind = Literal["ticket", "manager", "goal"]


class ConversationHistoryEntry(TypedDict):
    conversation_id: str
    created_at: int
    label: str


class ConversationHistoryPage(TypedDict):
    revision: str
    offset: int
    total: int
    active_index: int | None
    entries: list[ConversationHistoryEntry]
