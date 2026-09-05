"""Sprint domain shapes: sprints, sprint items, ideas, and derived item status."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypedDict

from planner.conversation.contracts import ConversationBackendKey
from planner.core.contracts import Priority


class ItemStatus(StrEnum):
    todo = "todo"
    in_progress = "in_progress"
    blocked = "blocked"
    done = "done"


class SprintItemKind(StrEnum):
    normal = "normal"
    other = "other"


@dataclass(frozen=True, slots=True)
class SprintItemSupervisorLaunchConfiguration:
    employee_backend: ConversationBackendKey
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None


# This snapshot is part of the Sprint Item domain. Migration code repeats these literal
# values so an upgrade never reads mutable Worker or Chief settings.
SPRINT_ITEM_SUPERVISOR_LAUNCH_DEFAULTS: Final = SprintItemSupervisorLaunchConfiguration(
    employee_backend=ConversationBackendKey.codex,
    employee_launch_model="gpt-5.6-sol",
    employee_launch_reasoning_effort="medium",
)


ITEM_STATUS_ORDER: Final[tuple[ItemStatus, ...]] = (
    ItemStatus.todo,
    ItemStatus.in_progress,
    ItemStatus.blocked,
    ItemStatus.done,
)

# Editable documents; their internal headings are prose, not stored fields.
SPRINT_DOCUMENT_FIELDS: Final[tuple[str, ...]] = ("kickoff", "checkpoint", "review")


@dataclass
class Sprint:  # §3.1
    id: str
    name: str
    date_start: str  # ISO, inclusive
    date_end: str  # ISO, inclusive
    primary_bet: str = ""  # summary also displayed above Sprint tracking
    kickoff: str = ""
    checkpoint: str = ""
    review: str = ""
    created_at: int = 0
    updated_at: int = 0


@dataclass
class SprintItem:  # §3.2
    id: str
    title: str
    body: str
    priority: Priority
    deadline: str | None
    project_id: str
    project_name: str
    sprint_id: str | None  # NULL = backlog/deferred
    supervisor_agent_key: str
    supervisor_launch_configuration: SprintItemSupervisorLaunchConfiguration
    kind: SprintItemKind = SprintItemKind.normal
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class SprintItemDeletion:
    """The deleted identity plus surviving resources affected by the transaction."""

    sprint_item_id: str
    title: str
    sprint_ids: tuple[str, ...]
    linked_entity_ids: tuple[str, ...]


# --- request bodies (§9 wire shapes) ---
# Every key is optional on the wire: an absent key takes the documented default,
# unknown keys are ignored except for Sprint creation, which rejects them.
# The api layer marshals the raw JSON dict into these
# shapes; a null or wrong-typed value raises ErrorCode.validation. Enum-valued
# keys carry the string form and are parsed against the contract enums in api.


class CreateItemBody(TypedDict, total=False):  # POST /items
    title: str  # default ""
    project: str | None  # legacy project name; route requires project or project_id
    project_id: str | None
    body: str  # default ""
    priority: str | None  # Priority value; default P3
    deadline: str | None  # ISO date
    sprint_id: str | None  # null/absent = backlog


class MoveItemTicketBody(TypedDict, total=False):  # POST /items/{id}/tickets
    ticket_id: str


class CreateSprintBody(TypedDict, total=False):  # POST /sprints
    name: str  # default ""
    date_start: str  # ISO date; required (default "" is rejected)
    date_end: str  # ISO date; required (default "" is rejected)
    primary_bet: str  # default ""
    kickoff: str  # default ""
    checkpoint: str  # default ""
    review: str  # default ""


class CreateIdeaBody(TypedDict, total=False):  # POST /ideas
    title: str  # route requires it non-empty
    body: str  # default ""
    project: str | None  # legacy project name
    project_id: str | None


@dataclass
class Idea:  # §3.5
    id: str
    title: str
    body: str
    project_id: str | None
    project_name: str | None
    created_at: int
    updated_at: int
