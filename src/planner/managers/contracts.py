"""Manager and Idea contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal, NotRequired, TypedDict

from planner.conversation.contracts import ConversationBackendKey


@dataclass(frozen=True, slots=True)
class ManagerLaunchConfiguration:
    employee_backend: ConversationBackendKey
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None


# The default selects a family; dispatch resolves its current installed version.
MANAGER_LAUNCH_DEFAULTS: Final = ManagerLaunchConfiguration(
    employee_backend=ConversationBackendKey.codex,
    employee_launch_model="sol",
    employee_launch_reasoning_effort="medium",
)


@dataclass
class Manager:  # §3.2
    id: str
    title: str
    goal: str
    recap: str
    project_id: str
    project_name: str
    manager_launch_configuration: ManagerLaunchConfiguration
    settled: bool = False
    created_at: int = 0
    updated_at: int = 0

    stored_agent_key: str | None = field(default=None, kw_only=True, repr=False)

    @property
    def manager_agent_key(self) -> str:
        """The Manager identity is the Manager identity in agent-key form."""
        return self.stored_agent_key or f"manager_{self.id}"


@dataclass(frozen=True)
class ManagerDeletion:
    """The deleted identity plus surviving resources affected by the transaction."""

    manager_id: str
    title: str


class ManagerWorkspaceDocument(TypedDict):
    """Selected document only; catalogs have separate bounded readers."""

    goal: str
    recap: str


class ManagerWorkspaceContent(ManagerWorkspaceDocument):
    """Exact content door without unopened artifact or conversation catalogs."""


# --- request bodies (§9 wire shapes) ---
# Every key is optional on the wire: an absent key takes the documented default,
# unknown keys are ignored.
# The api layer marshals the raw JSON dict into these
# shapes; a null or wrong-typed value raises ErrorCode.validation. Enum-valued
# keys carry the string form and are parsed against the contract enums in api.


class CreateManagerBody(TypedDict, total=False):  # POST /items
    title: str  # default ""
    project: str | None  # legacy project name; route requires project or project_id
    project_id: str | None
    goal: str  # default ""


class ManagerStartupResult(TypedDict):
    """Creation returns item detail plus startup: this result, or null for a blank brief.

    The manager is saved even when delivery fails. A conversation id identifies
    the attempted first message, not proof that the Manager received the brief.
    """

    conversation_id: str | None
    fate: Literal["started", "queued", "injected", "refused", "uncertain", "failed"]
    refusal_reason: NotRequired[str]
    queue_position: NotRequired[int]
    error: NotRequired[str]


class ManagerSummary(TypedDict):
    settled: bool
    id: str
    title: str
    project_id: str
    project: str
    created_at: int
    updated_at: int
