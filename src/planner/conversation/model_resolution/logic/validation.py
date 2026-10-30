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


def submission_validation(
    selection: ModelSelection,
    observation: CatalogObservation | None = None,
    *,
    disabled_models: frozenset[str] = frozenset(),
    previous_alias_resolution: AliasResolutionFact | None = None,
    unavailable: CatalogUnavailable | None = None,
) -> CanonicalModelValidation:
    """Bind authored intent; advertised capabilities never grant permission to emit."""
    token = selection.submitted_token
    kind = selection.selection_kind
    native_claude_alias = (
        selection.backend_key is ConversationBackendKey.claude
        and token.removesuffix("[1m]") in {"opus", "sonnet", "haiku"}
    )
    panels_alias = kind is SelectionKind.panels_alias or (
        kind is SelectionKind.legacy_unclassified
        and not native_claude_alias
        and (variant := recognize_variant(selection.backend_key, token)) is not None
        and variant.name == token
    )
    selected = None
    if panels_alias and not native_claude_alias:
        selected = (
            _alias_candidate(selection, observation.rows, disabled_models) if observation else None
        )
        if selected is None:
            return CanonicalModelValidation(
                selection,
                ValidationState.unresolved,
                ValidationReason.alias_mapping_unavailable,
                CorrectiveAction.choose_model,
                None,
                None,
                observation.evidence if observation else None,
                None,
                previous_alias_resolution,
                unavailable,
            )
        token = selected.submitted_token
    elif observation is not None:
        matches = [
            row for row in observation.rows if not row.hidden and row.submitted_token == token
        ]
        if matches and all(row_facts(row) == row_facts(matches[0]) for row in matches):
            selected = matches[0]
    # Resolved means advertised, never applied. Unknown aliases stay unknown.
    resolved = (
        (selected.resolved_model_id if selected is not None else None)
        if native_claude_alias
        else (
            token
            if kind is SelectionKind.concrete
            else selected.resolved_model_id
            if selected is not None
            else None
        )
    )
    if (
        panels_alias
        and not native_claude_alias
        and selected is not None
        and selected.selection_kind is SelectionKind.concrete
    ):
        resolved = token
    alias = (
        AliasResolutionFact(selection.submitted_token, resolved, observation.evidence)
        if observation is not None
        and resolved is not None
        and (
            kind in (SelectionKind.provider_alias, SelectionKind.panels_alias)
            or native_claude_alias
        )
        else None
    )
    disabled = selection.submitted_token in disabled_models or token in disabled_models
    reason = (
        ValidationReason.catalog_unavailable
        if observation is None
        else ValidationReason.unknown_model
        if selected is None
        else ValidationReason.unknown_effort
        if selection.reasoning_effort is not None and selected.supported_reasoning_efforts is None
        else ValidationReason.effort_incompatible
        if selection.reasoning_effort is not None
        and selection.reasoning_effort not in (selected.supported_reasoning_efforts or ())
        else ValidationReason.supported
    )
    if unavailable is not None:
        reason = unavailable.reason
    return CanonicalModelValidation(
        selection,
        ValidationState.incompatible
        if disabled or reason is ValidationReason.effort_incompatible
        else ValidationState.compatible
        if reason is ValidationReason.supported
        else ValidationState.unresolved,
        ValidationReason.model_incompatible if disabled else reason,
        CorrectiveAction.choose_model if disabled else CorrectiveAction.none,
        resolved,
        None if disabled else token,
        observation.evidence if observation else None,
        alias,
        previous_alias_resolution,
        unavailable,
    )


def reported_selection_blocks(
    validation: CanonicalModelValidation,
    *,
    reported_model: str,
    reported_effort: str | None,
    disabled_models: frozenset[str],
) -> bool:
    """Unknown native alias targets cannot establish a concrete mismatch."""
    native_alias = (
        validation.selection.backend_key is ConversationBackendKey.claude
        and validation.launch_token is not None
        and validation.launch_token.removesuffix("[1m]") in {"opus", "sonnet", "haiku"}
    )
    concrete = not native_alias and (
        validation.selection.selection_kind is SelectionKind.concrete
        or (
            validation.selection.selection_kind is SelectionKind.panels_alias
            and validation.launch_token == validation.resolved_model_id
        )
    )
    return (
        reported_model in disabled_models
        or (concrete and validation.launch_token != reported_model)
        or (
            validation.selection.reasoning_effort is not None
            and reported_effort is not None
            and validation.selection.reasoning_effort != reported_effort
        )
    )


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
    """Keep catalog attribution local to its observed execution owner.

    Freshness labels and effort advertisements are advisory. Missing resolution
    cannot reject an exact provider token. Wrong-owner rows cannot map an alias.
    """
    scoped = observation
    if observation is not None and (
        observation.evidence.context != context
        or (context.account_generation is None and observation.evidence.attempt_id != attempt_id)
    ):
        scoped = None
    result = submission_validation(
        selection,
        scoped,
        disabled_models=disabled_models,
        previous_alias_resolution=previous_alias_resolution,
    )
    reason = (
        ValidationReason.account_observation_required
        if observation is not None and scoped is None and observation.evidence.context == context
        else ValidationReason.context_changed
        if observation is not None and scoped is None
        else evidence_reason(scoped, context, now=now, attempt_id=attempt_id)
        if scoped is not None
        else None
    )
    if reason is not None and result.launch_token is not None:
        result = replace(result, state=ValidationState.unresolved, reason=reason)
    return result
