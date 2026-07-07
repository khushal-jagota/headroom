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
class CommandRunResult:            # what the gateway adapter returns per /command run
    reply_text: str
    session_key: str               # persisted onto the entity's chat_session_key (first run)
    kind: str                      # "assistant" (a model turn) | "system" (display output)


@dataclass(frozen=True)
class CommandCategory:             # one grouped section of the "/" menu (skills excluded)
    name: str
    pairs: tuple[tuple[str, str], ...]   # (command, description), catalog order


@dataclass(frozen=True)
class CommandCatalog:              # the gateway's own command registry, for the "/" menu
    categories: tuple[CommandCategory, ...]   # grouped commands, catalog order
    skills: tuple[tuple[str, str], ...]       # the skills group (pairs[-skill_count:])
    canon: dict[str, str]                     # "/alias" -> "/canonical" (lowercased keys)
    sub: dict[str, list[str]]                 # "/cmd" -> subcommand hints


@dataclass(frozen=True)
class GatewayStatus:               # availability signal for the panel (§11)
    available: bool
    detail: str | None = None      # e.g. "connection refused" — logged, not shown
