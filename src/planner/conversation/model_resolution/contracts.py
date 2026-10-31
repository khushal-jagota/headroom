"""The shared model-family resolution contract for creation, correction and dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Protocol

from pydantic import ConfigDict, Field

from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.snapshot import BackendSnapshot
from planner.execution_identity import ExecutorIdentity
from planner.tickets.contracts import TicketLocale


@dataclass(frozen=True, slots=True)
class ModelVariant:
    backend_key: ConversationBackendKey
    name: str


type NonemptyText = Annotated[str, Field(strict=True, min_length=1)]


class SelectionKind(StrEnum):
    concrete = "concrete"
    provider_alias = "provider_alias"
    panels_alias = "panels_alias"
    legacy_unclassified = "legacy_unclassified"


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """Exact requested intent. A resolved identity never replaces these fields.

    Revision identifies this selection, not arbitrary Ticket prose. Null effort
    requests the provider default. Legacy intent remains unclassified.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    destination: TicketLocale
    backend_key: ConversationBackendKey
    submitted_token: NonemptyText
    selection_kind: SelectionKind
    reasoning_effort: NonemptyText | None
    selection_revision: NonemptyText


@dataclass(frozen=True, slots=True)
class CatalogWorkspaceBinding:
    """Canonical workspace facts from the existing resolved start authority.

    These are the workspace_folder and policy from ResolvedConversationStart.
    workspace_home_relative retains the existing legacy transport representation:
    null means no mapping, an empty string means home, and other strings name a
    safe descendant. Native absolute policy never accepts a home mapping.
    The executor applies the same localization and directory checks as start.
    Missing or unsafe workspace yields unresolved evidence, never a cwd default.
    This binding grants no arbitrary caller authority over directories or commands.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    workspace_folder: Path
    workspace_path_policy: WorkspacePathPolicy
    workspace_home_relative: Annotated[str, Field(strict=True)] | None


class ValidationState(StrEnum):
    compatible = "compatible"
    incompatible = "incompatible"
    unresolved = "unresolved"


class ValidationReason(StrEnum):
    supported = "supported"
    model_incompatible = "model_incompatible"
    effort_incompatible = "effort_incompatible"
    catalog_unavailable = "catalog_unavailable"
    offline = "offline"
    timeout = "timeout"
    context_changed = "context_changed"
    alias_mapping_unavailable = "alias_mapping_unavailable"
    legacy_unscoped = "legacy_unscoped"
    identity_unproved = "identity_unproved"
    configuration_scope_unknown = "configuration_scope_unknown"


class CorrectiveAction(StrEnum):
    none = "none"
    refresh_catalog = "refresh_catalog"
    reconnect_destination = "reconnect_destination"
    choose_model = "choose_model"
    verify_launch_context = "verify_launch_context"
    classify_selection = "classify_selection"


@dataclass(frozen=True, slots=True)
class CanonicalModelValidation:
    """One selection revision's metadata outcome, never an admission capability.

    Admission rechecks exact intent and current explicit model disables.
    Compatible never proves provider entitlement.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    selection: ModelSelection
    state: ValidationState
    reason: ValidationReason
    corrective_action: CorrectiveAction
    launch_token: NonemptyText | None


class RemoteCatalogSource(Protocol):
    @property
    def identity(self) -> ExecutorIdentity | None: ...

    @property
    def ready(self) -> bool: ...

    def owns_conversation(self, conversation_id: str) -> bool: ...

    def retained(self, backend_key: ConversationBackendKey) -> BackendSnapshot | None: ...

    async def snapshot(
        self, backend_key: ConversationBackendKey, *, refresh: bool = False
    ) -> BackendSnapshot: ...
