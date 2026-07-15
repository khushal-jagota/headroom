"""Chat domain contracts for server-owned turns and gateway observations. Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

ChattableEntityKind: TypeAlias = Literal[  # noqa: UP040 -- frozen AD06 declaration
    "ticket", "day", "agent_chat_session"
]


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
class ChatPendingClarification:
    request_id: str
    question: str
    choices: tuple[str, ...] = ()


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
    can_pause: bool
    error: str | None
    started_at: int
    updated_at: int
    completed_at: int | None
    activity_entries: tuple[ChatActivityEntry, ...] = ()
    pending_clarification: ChatPendingClarification | None = None


@dataclass(frozen=True)
class ChatTurnOutcome:
    """Transcript projection for one durable failed or interrupted turn."""

    turn_id: str
    origin: str
    status: Literal["errored", "interrupted"]
    output_role: str
    output_text: str
    error: str | None
    can_continue: bool
    completed_at: int


@dataclass(frozen=True)
class ChatState:
    messages: tuple[ChatStateMessage, ...]
    outcomes: tuple[ChatTurnOutcome, ...]
    active_turn: ChatTurn | None


@dataclass(frozen=True)
class HumanChatOutputDelta:
    text: str


@dataclass(frozen=True)
class HumanChatCompletion:
    text: str
    role: Literal["assistant", "system"]


HumanChatObservation: TypeAlias = (  # noqa: UP040 -- frozen AD06 declaration
    ChatActivityObservation | HumanChatOutputDelta | HumanChatCompletion
)


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
