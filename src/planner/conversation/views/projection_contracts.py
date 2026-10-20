"""Small source-reference mutations used by the incremental projection writer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from planner.conversation.views.contracts import ViewRowKind

type LookupKind = Literal["tool", "permission", "user_input", "sender_message"]


@dataclass(frozen=True, slots=True)
class EventEffect:
    row_kind: ViewRowKind | None
    focus_visible: bool
    lookup_kind: LookupKind | None = None
    lookup_id: str | None = None
    settlement: bool = False
    opens_turn: bool = False
    closes_turn: bool = False
    changes_plan: bool = False
    sender_message_id: str | None = None
    reconciles_sequence: int | None = None
