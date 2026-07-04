"""Chat domain shapes: messages, the gateway send result, and the availability
signal for the panel (§11). Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMessage:
    role: str                      # "user" | "assistant"
    text: str
    created_at: int


@dataclass(frozen=True)
class ChatSendResult:              # what the gateway adapter returns per send
    reply_text: str
    session_key: str               # persisted onto the entity's chat_session_key


@dataclass(frozen=True)
class GatewayStatus:               # availability signal for the panel (§11)
    available: bool
    detail: str | None = None      # e.g. "connection refused" — logged, not shown
