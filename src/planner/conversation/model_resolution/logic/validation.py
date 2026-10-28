"""Pure exact-intent validation against one captured destination observation."""

from __future__ import annotations

import math
from dataclasses import replace

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.config import CATALOGUE_FRESH_SECONDS
from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CanonicalModelValidation,
    CatalogObservation,
    CatalogRow,
    CatalogUnavailable,
    CorrectiveAction,
    LaunchContext,
    ModelSelection,
    PrerequisiteStatus,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.conversation.model_resolution.logic.variants import (
    numerical_version,
    recognize_variant,
)


def unresolved_validation(
    selection: ModelSelection,
    reason: ValidationReason,
    *,
    unavailable: CatalogUnavailable | None = None,
    observation: CatalogObservation | None = None,
    previous_alias_resolution: AliasResolutionFact | None = None,
) -> CanonicalModelValidation:
    return CanonicalModelValidation(
        selection=selection,
        state=ValidationState.unresolved,
        reason=reason,
        corrective_action=(
            unavailable.corrective_action if unavailable else CorrectiveAction.refresh_catalog
        ),
        resolved_model_id=None,
        launch_token=None,
        evidence=observation.evidence if observation else None,
        current_alias_resolution=None,
        previous_alias_resolution=previous_alias_resolution,
        unavailable=unavailable,
    )


def evidence_reason(
    observation: CatalogObservation,
    context: LaunchContext,
    *,
    now: float,
    attempt_id: str,
) -> ValidationReason | None:
    """Receipt time, missing generations, and clock uncertainty never grant freshness."""
    evidence = observation.evidence
    if evidence.context != context:
        return ValidationReason.context_changed
    if context.account_generation is None and evidence.attempt_id != attempt_id:
        return ValidationReason.account_observation_required
    uncertainty = evidence.clock_uncertainty_seconds
    age = now - evidence.observed_at
    if (
        uncertainty is None
        or not math.isfinite(age)
        or not math.isfinite(uncertainty)
        or uncertainty < 0
        or age < 0
        or age + uncertainty >= CATALOGUE_FRESH_SECONDS
    ):
        return ValidationReason.stale_evidence
    if observation.installation is PrerequisiteStatus.available and (
        context.executable_identity is None or context.executable_version is None
    ):
        return ValidationReason.identity_unproved
    if observation.credentials is PrerequisiteStatus.available and context.provider_scope is None:
        return ValidationReason.configuration_scope_unknown
    return None


def row_facts(row: CatalogRow) -> CatalogRow:
    """Source labels are provenance, not a reason to erase identical capabilities."""
    return replace(row, source="record")


def _alias_candidate(
    selection: ModelSelection, rows: tuple[CatalogRow, ...], disabled_models: frozenset[str]
) -> CatalogRow | None:
    """Existing Panels policies select only advertised destination candidates."""
    variant = recognize_variant(selection.backend_key, selection.submitted_token)
    if variant is None or variant.name != selection.submitted_token:
        return None
    candidates = [
        row
        for row in rows
        if not row.hidden
        and row.enabled is not False
        and row.submitted_token not in disabled_models
        and row.resolved_model_id not in disabled_models
        and recognize_variant(selection.backend_key, row.submitted_token) == variant
    ]
    if not candidates:
        return None
    # Claude's existing policy prefers its provider alias when advertised.
    if selection.backend_key is ConversationBackendKey.claude:
        alias = next(
            (row for row in candidates if row.submitted_token == selection.submitted_token), None
        )
        if alias is not None:
            return alias
    return max(candidates, key=lambda row: numerical_version(row.submitted_token))


def validate_model_selection(
    selection: ModelSelection,
    context: LaunchContext,
    observation: CatalogObservation | None,
    *,
    now: float,
    attempt_id: str,
    disabled_models: frozenset[str] = frozenset(),
    previous_alias_resolution: AliasResolutionFact | None = None,
) -> CanonicalModelValidation:
    """Return metadata facts only. Final admission must recheck intent and context."""
    if previous_alias_resolution is not None and (
        previous_alias_resolution.submitted_token != selection.submitted_token
        or selection.selection_kind
        not in (SelectionKind.provider_alias, SelectionKind.panels_alias)
    ):
        previous_alias_resolution = None

    def unresolved(reason: ValidationReason) -> CanonicalModelValidation:
        return unresolved_validation(
            selection,
            reason,
            observation=observation,
            previous_alias_resolution=previous_alias_resolution,
        )

    if (selection.destination, selection.backend_key) != (
        context.destination,
        context.backend_key,
    ):
        return unresolved(ValidationReason.context_changed)
    if selection.submitted_token in disabled_models:
        return replace(
            unresolved(ValidationReason.model_incompatible),
            state=ValidationState.incompatible,
            corrective_action=CorrectiveAction.choose_model,
        )
    if observation is None:
        return unresolved(ValidationReason.catalog_unavailable)
    freshness = evidence_reason(observation, context, now=now, attempt_id=attempt_id)
    if freshness is not None:
        return unresolved(freshness)
    for status, reason, action in (
        (
            observation.installation,
            ValidationReason.installation_unavailable,
            CorrectiveAction.install_provider,
        ),
        (
            observation.credentials,
            ValidationReason.credentials_unavailable,
            CorrectiveAction.authenticate_provider,
        ),
    ):
        if status is PrerequisiteStatus.unavailable:
            return replace(
                unresolved(reason), state=ValidationState.unavailable, corrective_action=action
            )
        if status is PrerequisiteStatus.unknown:
            return unresolved(ValidationReason.catalog_unavailable)
    if selection.selection_kind is SelectionKind.legacy_unclassified:
        return unresolved(ValidationReason.legacy_unscoped)
    rows = tuple(row for row in observation.rows if not row.hidden)
    matches = tuple(row for row in rows if row.submitted_token == selection.submitted_token)
    selected: CatalogRow | None = None
    if selection.selection_kind is SelectionKind.panels_alias:
        selected = _alias_candidate(selection, rows, disabled_models)
        if selected is not None:
            matches = tuple(row for row in rows if row.submitted_token == selected.submitted_token)
    elif matches:
        selected = matches[0]
    elif selection.selection_kind is SelectionKind.concrete:
        # An alias target is usable as an exact token only with affirmative adapter proof.
        matches = tuple(
            row
            for row in rows
            if row.resolved_model_id == selection.submitted_token
            and row.identity_preserving_launch_token == selection.submitted_token
        )
        selected = matches[0] if matches else None
    if matches and any(row_facts(row) != row_facts(matches[0]) for row in matches[1:]):
        return unresolved(ValidationReason.conflicting_rows)
    if selected is None:
        # No family allowlist can turn unfamiliar advertised tokens into a negative.
        if any(row.resolved_model_id == selection.submitted_token for row in rows):
            return unresolved(ValidationReason.identity_unproved)
        if (
            selection.selection_kind is SelectionKind.concrete
            and observation.complete
            and observation.absence_authoritative
            and not observation.visibility_limited
            and not any(row.hidden for row in observation.rows)
        ):
            return replace(
                unresolved(ValidationReason.model_incompatible),
                state=ValidationState.incompatible,
                corrective_action=CorrectiveAction.choose_model,
            )
        reason = (
            ValidationReason.partial_catalog
            if not observation.complete
            else ValidationReason.visibility_limited
            if observation.visibility_limited
            else ValidationReason.unknown_model
        )
        return unresolved(reason)
    if selection.selection_kind is SelectionKind.provider_alias and (
        selected.selection_kind is not SelectionKind.provider_alias
    ):
        return unresolved(ValidationReason.identity_unproved)
    if selection.selection_kind is SelectionKind.concrete:
        if selected.submitted_token == selection.submitted_token and (
            selected.selection_kind is not SelectionKind.concrete
            or selected.resolved_model_id not in (None, selection.submitted_token)
        ):
            return unresolved(ValidationReason.identity_unproved)
        resolved_id = selection.submitted_token
        launch_token = selection.submitted_token
    else:
        own_id = selected.resolved_model_id
        if own_id is None and selected.selection_kind is not SelectionKind.concrete:
            return unresolved(ValidationReason.identity_unproved)
        resolved_id = own_id or selected.submitted_token
        launch_token = selected.submitted_token
    alias = (
        AliasResolutionFact(selection.submitted_token, resolved_id, observation.evidence)
        if selection.selection_kind in (SelectionKind.provider_alias, SelectionKind.panels_alias)
        else None
    )
    base = CanonicalModelValidation(
        selection=selection,
        state=ValidationState.compatible,
        reason=ValidationReason.supported,
        corrective_action=CorrectiveAction.none,
        resolved_model_id=resolved_id,
        launch_token=launch_token,
        evidence=observation.evidence,
        current_alias_resolution=alias,
        previous_alias_resolution=previous_alias_resolution,
        unavailable=None,
    )
    if (
        selected.enabled is False
        or selection.submitted_token in disabled_models
        or selected.submitted_token in disabled_models
        or resolved_id in disabled_models
    ):
        return replace(
            base,
            state=ValidationState.incompatible,
            reason=ValidationReason.model_incompatible,
            corrective_action=CorrectiveAction.choose_model,
            launch_token=None,
        )
    if selection.reasoning_effort is not None:
        support = selected.supported_reasoning_efforts
        if support is None:
            return replace(
                base,
                state=ValidationState.unresolved,
                reason=ValidationReason.unknown_effort,
                corrective_action=CorrectiveAction.refresh_catalog,
                launch_token=None,
            )
        if selection.reasoning_effort not in support:
            return replace(
                base,
                state=ValidationState.incompatible,
                reason=ValidationReason.effort_incompatible,
                corrective_action=CorrectiveAction.choose_effort,
                launch_token=None,
            )
    return base
