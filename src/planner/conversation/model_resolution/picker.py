"""Truthful destination picker facts, independent of labels and lifecycle policy."""

from __future__ import annotations

from dataclasses import replace

from pydantic import TypeAdapter

from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
    CanonicalModelValidation,
    CatalogObservation,
    CatalogPresence,
    CatalogRow,
    CatalogRowReference,
    CatalogUnavailable,
    EvidenceStatus,
    LaunchContext,
    ModelSelection,
    PickerRow,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.conversation.model_resolution.logic.validation import (
    evidence_reason,
    row_facts,
    unresolved_validation,
    validate_model_selection,
)
from planner.core.contracts import JsonDict


def project_picker_rows(
    selection: ModelSelection,
    context: LaunchContext,
    observation: CatalogObservation | None,
    *,
    now: float,
    attempt_id: str,
    previous_alias_resolution: AliasResolutionFact | None = None,
    disabled_models: frozenset[str] = frozenset(),
    unavailable: CatalogUnavailable | None = None,
) -> tuple[PickerRow, ...]:
    """Retain requested historical values separately from current destination offers."""
    applicable = (
        observation is not None
        and observation.evidence.context == context
        and (
            context.destination == selection.destination
            and context.backend_key == selection.backend_key
        )
    )

    def with_failure(validation: CanonicalModelValidation) -> CanonicalModelValidation:
        if unavailable is None:
            return validation
        if (
            unavailable.destination != selection.destination
            or unavailable.backend_key != selection.backend_key
            or (
                unavailable.attempted_context is not None
                and unavailable.attempted_context != context
            )
        ):
            return unresolved_validation(validation.selection, ValidationReason.context_changed)
        state = (
            ValidationState.unavailable
            if unavailable.reason
            in (
                ValidationReason.installation_unavailable,
                ValidationReason.credentials_unavailable,
            )
            else ValidationState.unresolved
        )
        return replace(
            validation,
            state=state,
            reason=unavailable.reason,
            corrective_action=unavailable.corrective_action,
            unavailable=unavailable,
        )

    groups: dict[CatalogRow, list[int]] = {}
    if applicable and observation is not None:
        for ordinal, row in enumerate(observation.rows):
            if not row.hidden:
                groups.setdefault(row_facts(row), []).append(ordinal)
    result: list[PickerRow] = []
    selected_visible = False
    for row, ordinals in groups.items():
        assert observation is not None
        is_selected = (
            row.submitted_token == selection.submitted_token
            and row.selection_kind is selection.selection_kind
        )
        selected_visible |= is_selected
        row_selection = replace(
            selection, submitted_token=row.submitted_token, selection_kind=row.selection_kind
        )
        validation = validate_model_selection(
            row_selection,
            context,
            observation,
            now=now,
            attempt_id=attempt_id,
            disabled_models=disabled_models,
            previous_alias_resolution=previous_alias_resolution if is_selected else None,
        )
        validation = with_failure(validation)
        reason = evidence_reason(observation, context, now=now, attempt_id=attempt_id)
        status = (
            EvidenceStatus.fresh
            if reason is None
            else EvidenceStatus.stale
            if reason is ValidationReason.stale_evidence
            else EvidenceStatus.unverified
        )
        if validation.unavailable is not None:
            status = EvidenceStatus.unavailable
        result.append(
            PickerRow(
                submitted_token=row.submitted_token,
                selection_kind=row.selection_kind,
                resolved_model_id=row.resolved_model_id,
                display_name=row.display_name,
                supported_reasoning_efforts=row.supported_reasoning_efforts,
                context=observation.evidence.context,
                observed_at=observation.evidence.observed_at,
                evidence_status=status,
                catalog_presence=CatalogPresence.present,
                current_selection=is_selected,
                current_alias_resolution=validation.current_alias_resolution,
                previous_alias_resolution=validation.previous_alias_resolution,
                provenance=tuple(
                    CatalogRowReference(observation.evidence, ordinal) for ordinal in ordinals
                ),
                validation=validation,
            )
        )
    if not selected_visible:
        validation = validate_model_selection(
            selection,
            context,
            observation,
            now=now,
            attempt_id=attempt_id,
            disabled_models=disabled_models,
            previous_alias_resolution=previous_alias_resolution,
        )
        validation = with_failure(validation)
        absent = (
            validation.state is ValidationState.incompatible
            and validation.reason is ValidationReason.model_incompatible
            and selection.selection_kind is SelectionKind.concrete
            and applicable
            and observation is not None
            and observation.complete
            and observation.absence_authoritative
            and not observation.visibility_limited
            and not any(row.hidden for row in observation.rows)
            and not any(
                row.submitted_token == selection.submitted_token for row in observation.rows
            )
        )
        result.append(
            PickerRow(
                submitted_token=selection.submitted_token,
                selection_kind=selection.selection_kind,
                resolved_model_id=validation.resolved_model_id,
                display_name=None,
                supported_reasoning_efforts=None,
                context=context,
                observed_at=(
                    observation.evidence.observed_at if applicable and observation else None
                ),
                evidence_status=(
                    EvidenceStatus.unavailable
                    if validation.unavailable
                    else EvidenceStatus.unverified
                ),
                catalog_presence=CatalogPresence.absent if absent else CatalogPresence.unknown,
                current_selection=True,
                current_alias_resolution=validation.current_alias_resolution,
                previous_alias_resolution=validation.previous_alias_resolution,
                provenance=(),
                validation=validation,
            )
        )
    return tuple(result)


def serialize_picker_row(row: PickerRow) -> JsonDict:
    """Mirror shared contracts as JSON, including null effort and full safe provenance."""
    value = TypeAdapter(PickerRow).dump_python(row, mode="json")
    assert isinstance(value, dict)
    return value
