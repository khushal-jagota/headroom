"""Sprint domain shapes: Sprints, durable Outcomes, commitments, and ideas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, NotRequired, TypedDict

from planner.conversation.contracts import ConversationBackendKey


@dataclass(frozen=True, slots=True)
class SprintItemSupervisorLaunchConfiguration:
    employee_backend: ConversationBackendKey
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None


# The default selects a family; dispatch resolves its current installed version.
SPRINT_ITEM_SUPERVISOR_LAUNCH_DEFAULTS: Final = SprintItemSupervisorLaunchConfiguration(
    employee_backend=ConversationBackendKey.codex,
    employee_launch_model="sol",
    employee_launch_reasoning_effort="medium",
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
    project_id: str
    project_name: str
    supervisor_launch_configuration: SprintItemSupervisorLaunchConfiguration
    settled: bool = False
    created_at: int = 0
    updated_at: int = 0

    @property
    def supervisor_agent_key(self) -> str:
        """The supervisor identity is the Sprint Item identity in agent-key form."""
        return f"sprint_item_supervisor_{self.id}"


@dataclass(frozen=True)
class SprintItemDeletion:
    """The deleted identity plus surviving resources affected by the transaction."""

    sprint_item_id: str
    title: str
    sprint_ids: tuple[str, ...]


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


class OrchestratorStartupResult(TypedDict):
    """Creation returns item detail plus startup: this result, or null for a blank brief.

    The orchestrator is saved even when delivery fails. A conversation id identifies
    the attempted first message, not proof that the supervisor received the brief.
    """

    conversation_id: str | None
    fate: Literal["started", "queued", "injected", "refused", "uncertain", "failed"]
    refusal_reason: NotRequired[str]
    queue_position: NotRequired[int]
    error: NotRequired[str]


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


class SprintSummary(TypedDict):
    id: str
    name: str
    date_start: str
    date_end: str


class SprintWireBody(SprintSummary):
    primary_bet: str
    kickoff: str
    checkpoint: str
    review: str
    created_at: int
    updated_at: int


class OutcomeSummary(TypedDict):
    settled: bool
    id: str
    title: str
    project_id: str
    project: str
    created_at: int
    updated_at: int


class SprintTicketSummary(TypedDict):
    id: str
    title: str
    stage: str
    ticket_status: str
    project_id: str | None
    sprint_item_id: str | None
    waiting_to_closeout: bool


class SprintOutcomeGroup(TypedDict):
    outcome: OutcomeSummary
    committed: bool
    tickets: list[SprintTicketSummary]


class SprintTrackingBody(TypedDict):
    planning_date: str
    sprint: SprintWireBody | None
    outcome_groups: list[SprintOutcomeGroup]
    unclassified_tickets: list[SprintTicketSummary]
