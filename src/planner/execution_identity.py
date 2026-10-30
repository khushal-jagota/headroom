"""Stable executor, child and callback identities shared across module boundaries."""

from dataclasses import dataclass
from enum import StrEnum

from planner.conversation.backends.contracts import TurnToken


@dataclass(frozen=True, slots=True)
class ExecutorIdentity:
    protocol_version: int
    application_revision: str
    spool_format_version: int
    executor_incarnation: str
    connection_generation: int


@dataclass(frozen=True, slots=True)
class ChildReference:
    """Child IDs are UUIDs minted at child creation, never vendor session IDs."""

    executor_incarnation: str
    child_id: str
    conversation_id: str


class AskKind(StrEnum):
    permission = "permission"
    user_input = "user_input"


@dataclass(frozen=True, slots=True)
class AskReference:
    child: ChildReference
    turn: TurnToken
    ask_id: str
    kind: AskKind
