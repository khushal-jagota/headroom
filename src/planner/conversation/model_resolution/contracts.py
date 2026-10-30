"""The shared model-family resolution contract for creation, correction and dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Protocol

from pydantic import ConfigDict, Field

from planner.conversation.backends.codex_app_server.login import CodexLoginGeneration
from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.execution_identity import ExecutorIdentity
from planner.tickets.contracts import TicketLocale


@dataclass(frozen=True, slots=True)
class ModelVariant:
    backend_key: ConversationBackendKey
    name: str


# Metadata payloads live here so the executor contract does not depend on model
# resolution. Neither these payloads nor their request IDs confer child authority.
type NonemptyText = Annotated[str, Field(strict=True, min_length=1)]
type ObservationTime = Annotated[int, Field(strict=True, ge=0)]


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


@dataclass(frozen=True, slots=True)
class CatalogExpectedContext:
    """Captured executor identity and the canonically authorized workspace binding."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    executor_identity: ExecutorIdentity
    workspace: CatalogWorkspaceBinding


@dataclass(frozen=True, slots=True)
class LaunchContext:
    """Destination observation ownership, including the authorized workspace.

    The opaque reference belongs to a runtime or executor owner. Current discovery
    does not fingerprint full configuration, environment, or executable content.
    Optional proof fields remain readable for immutable historical observations.
    They grant no submission or provider-acceptance authority.
    No credential bytes, raw environment, or private configuration belongs here.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    context_ref: NonemptyText
    destination: TicketLocale
    backend_key: ConversationBackendKey
    executor_identity: ExecutorIdentity | None
    workspace: CatalogWorkspaceBinding
    localized_workspace_folder: Path
    executable_identity: NonemptyText | None
    executable_version: NonemptyText | None
    provider_scope: NonemptyText | None
    account_generation: CodexLoginGeneration | None


class EvidenceStatus(StrEnum):
    fresh = "fresh"
    stale = "stale"
    unverified = "unverified"
    unavailable = "unavailable"


class CatalogPresence(StrEnum):
    present = "present"
    absent = "absent"
    unknown = "unknown"


class ValidationState(StrEnum):
    compatible = "compatible"
    incompatible = "incompatible"
    unavailable = "unavailable"
    unresolved = "unresolved"


class ValidationReason(StrEnum):
    supported = "supported"
    model_incompatible = "model_incompatible"
    effort_incompatible = "effort_incompatible"
    installation_unavailable = "installation_unavailable"
    credentials_unavailable = "credentials_unavailable"
    catalog_unavailable = "catalog_unavailable"
    offline = "offline"
    timeout = "timeout"
    context_changed = "context_changed"
    stale_evidence = "stale_evidence"
    partial_catalog = "partial_catalog"
    visibility_limited = "visibility_limited"
    unknown_model = "unknown_model"
    unknown_effort = "unknown_effort"
    alias_mapping_unavailable = "alias_mapping_unavailable"
    legacy_unscoped = "legacy_unscoped"
    conflicting_rows = "conflicting_rows"
    identity_unproved = "identity_unproved"
    account_observation_required = "account_observation_required"
    configuration_scope_unknown = "configuration_scope_unknown"


class CorrectiveAction(StrEnum):
    none = "none"
    refresh_catalog = "refresh_catalog"
    reconnect_destination = "reconnect_destination"
    update_executor = "update_executor"
    install_provider = "install_provider"
    authenticate_provider = "authenticate_provider"
    choose_model = "choose_model"
    choose_effort = "choose_effort"
    verify_launch_context = "verify_launch_context"
    classify_selection = "classify_selection"


@dataclass(frozen=True, slots=True)
class CatalogEvidenceReference:
    """Original source evidence. Receipt time never renews observed_at.

    attempt_id owns the observation. Without account generation, another attempt
    cannot reuse this evidence or share its in-flight discovery. Unknown clock
    uncertainty or future source time cannot extend the 300-second freshness bound.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    observation_id: NonemptyText
    observation_revision: NonemptyText
    context: LaunchContext
    observed_at: ObservationTime
    attempt_id: NonemptyText
    clock_uncertainty_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None


@dataclass(frozen=True, slots=True)
class CatalogRowReference:
    """An exact source record, including duplicate rows in one observation."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    evidence: CatalogEvidenceReference
    row_ordinal: Annotated[int, Field(strict=True, ge=0)]


@dataclass(frozen=True, slots=True)
class AliasResolutionFact:
    """An alias target with its own context and original evidence provenance."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    submitted_token: NonemptyText
    resolved_model_id: NonemptyText
    evidence: CatalogEvidenceReference


@dataclass(frozen=True, slots=True)
class CatalogRow:
    """One provider record. Conflicting duplicates retain separate ordinals.

    Null effort support means unknown. An empty tuple means known empty support.
    An alias target alone does not prove that a concrete launch preserves identity.
    identity_preserving_launch_token requires affirmative adapter evidence.
    Advertisement never proves account entitlement. Hidden rows stay hidden.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    submitted_token: NonemptyText
    selection_kind: SelectionKind
    resolved_model_id: NonemptyText | None
    display_name: NonemptyText | None
    supported_reasoning_efforts: tuple[NonemptyText, ...] | None
    hidden: bool
    enabled: bool | None
    identity_preserving_launch_token: NonemptyText | None
    source: NonemptyText


class PrerequisiteStatus(StrEnum):
    available = "available"
    unavailable = "unavailable"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class CatalogObservation:
    """Scoped read-only discovery, including useful positive partial results.

    Complete means every page settled. Absence proves incompatibility only when
    absence_authoritative is true and visibility_limited is false. All rows belong
    to this unchanged launch context. Changed context produces CatalogUnavailable.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    evidence: CatalogEvidenceReference
    rows: tuple[CatalogRow, ...]
    complete: bool
    visibility_limited: bool
    absence_authoritative: bool
    installation: PrerequisiteStatus
    credentials: PrerequisiteStatus
    kind: Literal["observation"] = "observation"


@dataclass(frozen=True, slots=True)
class CatalogUnavailable:
    """Safe failure provenance. Retained evidence keeps its original age and scope.

    Null context means that capture failed, not a default VPS context. Failure
    details contain no raw provider stderr, environment, or credential material.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    backend_key: ConversationBackendKey
    destination: TicketLocale
    attempted_context: LaunchContext | None
    reason: ValidationReason
    corrective_action: CorrectiveAction
    detail: NonemptyText | None
    observed_at: ObservationTime
    retained_evidence: CatalogEvidenceReference | None
    kind: Literal["unavailable"] = "unavailable"


type CatalogResult = Annotated[CatalogObservation | CatalogUnavailable, Field(discriminator="kind")]


@dataclass(frozen=True, slots=True)
class CanonicalModelValidation:
    """One selection revision's metadata outcome, never an admission capability.

    Admission must recheck this exact selection, launch context, freshness, and
    attempt ownership. Compatible means metadata support, never entitlement.
    Current and previous alias targets remain factual inputs for visible views.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    selection: ModelSelection
    state: ValidationState
    reason: ValidationReason
    corrective_action: CorrectiveAction
    resolved_model_id: NonemptyText | None
    launch_token: NonemptyText | None
    evidence: CatalogEvidenceReference | None
    current_alias_resolution: AliasResolutionFact | None
    previous_alias_resolution: AliasResolutionFact | None
    unavailable: CatalogUnavailable | None


@dataclass(frozen=True, slots=True)
class PickerRow:
    """Destination offer or retained selection, with its own unmodified facts.

    Coalesce only identical scoped intent, token, resolution, capabilities, and
    applicable evidence. Keep provenance for every contributing source record.
    Presentation must expose alias changes and structured unavailable validation.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    submitted_token: NonemptyText
    selection_kind: SelectionKind
    resolved_model_id: NonemptyText | None
    display_name: NonemptyText | None
    supported_reasoning_efforts: tuple[NonemptyText, ...] | None
    context: LaunchContext | None
    observed_at: ObservationTime | None
    evidence_status: EvidenceStatus
    catalog_presence: CatalogPresence
    current_selection: bool
    current_alias_resolution: AliasResolutionFact | None
    previous_alias_resolution: AliasResolutionFact | None
    provenance: tuple[CatalogRowReference, ...]
    validation: CanonicalModelValidation


@dataclass(frozen=True, slots=True)
class CatalogRequest:
    """Authenticated metadata request. The executor selects its launch settings.

    The expected context supplies canonical workspace facts and executor identity.
    The executor selects its CLI and environment. The VPS retains selection and
    attempt IDs. No command or child authority travels in this payload.

    catalog_cancel uses this identical payload and the original request_id. It
    binds backend, workspace context, captured writer, and connection generation.
    Cancellation is idempotent and revokes the observation before cleanup.
    Duplicate cancellation and late replies cannot restore revoked evidence.
    A replacement request cannot inherit the old observation or cleanup capacity.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    backend_key: ConversationBackendKey
    expected_context: CatalogExpectedContext


@dataclass(frozen=True, slots=True)
class CatalogResponse:
    """Nonjournaled reply, accepted only for the captured ready connection.

    The VPS verifies backend, writer, executor identity, connection generation,
    request ID, schema, workspace binding, and localized context. Late replies
    confer no authority, including replies after catalog_cancel or replacement.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    backend_key: ConversationBackendKey
    executor_identity: ExecutorIdentity
    result: CatalogResult


class ProviderCatalogObserver(Protocol):
    """Read-only provider discovery in the actual local production launch context.

    Observe records the actual authorized launch owner and workspace. It performs
    no configuration-proof probe, update check, or provider prompt. deadline is
    absolute monotonic time, never a wire field.
    A remote observer uses its own request ID as attempt_id. VPS correlation binds
    that ID to its caller's attempt and never shares missing-generation evidence.

    Timeout, cancellation, disconnect, and quiescence settle owned tasks and probe
    processes before capacity release. Cancellation cannot abandon physical cleanup.
    A caller can stop waiting, but cleanup ownership persists until settlement.
    Other children, their transport, and their journal remain independent.
    """

    async def capture_context(
        self, backend_key: ConversationBackendKey, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext: ...

    async def observe(
        self,
        backend_key: ConversationBackendKey,
        *,
        workspace: CatalogWorkspaceBinding,
        attempt_id: str,
        deadline: float,
    ) -> CatalogResult: ...
