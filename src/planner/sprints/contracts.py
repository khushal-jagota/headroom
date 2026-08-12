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

# Sprint text-field groups: the kickoff and review sub-fields. Freeze is retired,
# so these no longer gate writes — they only enumerate the always-editable sprint
# text fields (reused by _SPRINT_TEXT_FIELDS in data.py + api.py).
KICKOFF_FIELDS: Final[tuple[str, ...]] = (
    "limiting_factor",
    "primary_bet",
    "supports",
    "premortem",
)
REVIEW_FIELDS: Final[tuple[str, ...]] = (
    "outcomes",
    "solo_reflection",
    "joint_discussion",
    "updates_to_thinking",
    "carry_forward",
)
# Checkpoint (rev6): three headed markdown sub-fields on the sprint, edited per-field
# in place. The historical mid_* identifiers remain. All sprint text fields stay editable.
MID_SPRINT_FIELDS: Final[tuple[str, ...]] = (
    "mid_where_we_stand",
    "mid_whats_changed",
    "mid_what_to_adjust",
)


@dataclass
class Sprint:  # §3.1
    id: str
    name: str
    date_start: str  # ISO, inclusive
    date_end: str  # ISO, inclusive
    limiting_factor: str
    primary_bet: str
    supports: str
    premortem: str
    mid_where_we_stand: str = ""  # Checkpoint sub-fields; historical mid_* identifiers
    mid_whats_changed: str = ""
    mid_what_to_adjust: str = ""
    outcomes: str = ""
    solo_reflection: str = ""
    joint_discussion: str = ""
    updates_to_thinking: str = ""
    carry_forward: str = ""
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
# unknown keys are ignored. The api layer marshals the raw JSON dict into these
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
    limiting_factor: str  # default ""
    primary_bet: str  # default ""
    supports: str  # default ""
    premortem: str  # default ""


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
