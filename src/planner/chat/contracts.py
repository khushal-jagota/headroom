"""Chat domain contracts for server-owned turns and gateway observations. Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatTurnRequest:
    text: str
    mode: str = "message"
    image_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatStateMessage:
    id: int
    role: str                      # product role: "human" | "assistant" | "system" | "worker"
    text: str
    created_at: int
    turn_id: str | None = None


@dataclass(frozen=True)
class ChatActivityObservation:
    """Display-safe activity accepted from a normalized gateway event."""

    category: str                  # "thinking" | "tool" | "command"
    label: str
    lifecycle_state: str           # "running" | "complete"
    action_identity: str | None = None


@dataclass(frozen=True)
class ChatActivityEntry:
    """One ordered, persisted activity item belonging to a running turn."""

    id: int
    action_identity: str | None
    category: str
    label: str
    lifecycle_state: str
    started_at: int
    updated_at: int
    completed_at: int | None


@dataclass(frozen=True)
class ChatTurn:
    id: str
    entity_id: str
    origin: str                    # "human" | "worker" | "system"
    mode: str                      # "message" | "command" | "worker_step"
    status: str                    # "running" | "complete" | "errored" | "interrupted"
    phase: str                     # "queued" | "thinking" | "doing" | "responding" | "settled"
    activity_label: str | None
    output_role: str               # "assistant" | "system"
    output_text: str
    session_key: str | None
    error: str | None
    started_at: int
    updated_at: int
    completed_at: int | None
    activity_entries: tuple[ChatActivityEntry, ...] = ()


@dataclass(frozen=True)
class ChatState:
    messages: tuple[ChatStateMessage, ...]
    active_turn: ChatTurn | None
    session_key: str | None


@dataclass(frozen=True)
class ChatMessage:
    role: str                      # gateway role: "user" | "assistant" | "system" | ...
    text: str
    created_at: int


@dataclass(frozen=True)
class ChatHistory:
    messages: tuple[ChatMessage, ...]
    session_key: str | None        # the durable Hermes key whose history was read, if any


@dataclass(frozen=True)
class ChatStreamChunk:
    """One normalized gateway observation consumed by the server-owned human turn."""

    type: str                      # "session" | "activity" | "token" | "done"
    text: str = ""                 # token text, or activity label when type == "activity"
    reply_text: str = ""           # complete reply when type == "done"
    session_key: str = ""          # minted/resumed key when type == "session" or "done"
    kind: str = "assistant"        # "assistant" | "system" when type == "done"
    activity: ChatActivityObservation | None = None   # structured activity when type == "activity"


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
