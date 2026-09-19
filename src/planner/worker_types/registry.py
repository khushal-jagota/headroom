"""Validated registration and manifest serialization for Worker types."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from planner.conversation.contracts import require_conversation_backend_key
from planner.core.contracts import ErrorCode, JsonDict, PlannerError
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    WorkerTypeDefinition,
    WorkerTypeManifest,
    WorkerTypeManifestField,
    WorkerTypeManifestStage,
)

KNOWN_TOOLSET_PROFILES = frozenset({"default"})


def validate_definition(
    definition: WorkerTypeDefinition,
    *,
    known_skills: frozenset[str],
    known_toolset_profiles: frozenset[str],
) -> None:
    worker_type = definition.worker_type

    def fail(message: str, detail: JsonDict) -> PlannerError:
        return PlannerError(ErrorCode.validation, message, detail)

    if len(definition.stages) < 1:
        raise fail("definition has no stages", {"worker_type": worker_type})
    if len(definition.fields) < 1:
        raise fail("definition has no fields", {"worker_type": worker_type})

    seen_stages: set[str] = set()
    for stage in definition.stages:
        if stage.id in seen_stages:
            raise fail(
                "duplicate stage id",
                {"worker_type": worker_type, "stage": stage.id},
            )
        seen_stages.add(stage.id)

    seen_fields: set[str] = set()
    for field in definition.fields:
        if field.id in seen_fields:
            raise fail(
                "duplicate field id",
                {"worker_type": worker_type, "field": field.id},
            )
        seen_fields.add(field.id)

    first = definition.stages[0]
    last = definition.stages[-1]

    kickoff_stage_indexes = [
        index for index, stage in enumerate(definition.stages) if stage.id == "needs_kickoff"
    ]
    kickoff_field_indexes = [
        index for index, field in enumerate(definition.fields) if field.id == "kickoff"
    ]
    if (
        kickoff_stage_indexes not in ([], [0])
        or kickoff_field_indexes not in ([], [0])
        or bool(kickoff_stage_indexes) != bool(kickoff_field_indexes)
        or (kickoff_stage_indexes and first.gating_field != "kickoff")
    ):
        raise fail(
            "kickoff stage and field must be paired first",
            {"worker_type": worker_type},
        )

    terminals = [stage for stage in definition.stages if stage.is_terminal is True]
    if len(terminals) != 1 or terminals[0] is not last:
        offending = terminals[0].id if terminals else last.id
        raise fail(
            "linear order must have exactly one terminal",
            {"worker_type": worker_type, "stage": offending},
        )

    if last.id != "done":
        raise fail(
            "last stage must be done",
            {"worker_type": worker_type, "last": last.id},
        )

    for stage in definition.stages:
        if stage.id == "dropped":
            raise fail(
                "dropped is not a stage",
                {"worker_type": worker_type, "stage": "dropped"},
            )
    for stage in definition.stages[:-1]:
        if stage.id == "done":
            raise fail(
                "done may not be a mid stage",
                {"worker_type": worker_type, "stage": "done"},
            )
    if last.gating_field is not None:
        raise fail(
            "terminal stage may not gate a field",
            {"worker_type": worker_type, "stage": "done"},
        )

    for stage in definition.stages:
        if not stage.is_terminal and stage.gating_field is None:
            raise fail(
                "non-terminal stage must gate a field",
                {"worker_type": worker_type, "stage": stage.id},
            )
        if not stage.is_terminal and stage.ownership_mode is None:
            raise fail(
                "non-terminal stage must declare ownership",
                {"worker_type": worker_type, "stage": stage.id},
            )
        if stage.is_terminal and stage.ownership_mode is not None:
            raise fail(
                "terminal stage may not declare ownership",
                {"worker_type": worker_type, "stage": stage.id},
            )
        if stage.ownership_mode is not None and not isinstance(
            stage.ownership_mode, StageOwnershipMode
        ):
            raise fail(
                "stage ownership must be a known mode",
                {"worker_type": worker_type, "stage": stage.id},
            )

    declared_field_ids = {field.id for field in definition.fields}
    for stage in definition.stages:
        if not stage.is_terminal and stage.gating_field not in declared_field_ids:
            raise fail(
                "gating field references an undeclared field",
                {
                    "worker_type": worker_type,
                    "stage": stage.id,
                    "gating_field": stage.gating_field,
                },
            )

    gated_count: dict[str, int] = {}
    for stage in definition.stages:
        if not stage.is_terminal and stage.gating_field is not None:
            gated_count[stage.gating_field] = gated_count.get(stage.gating_field, 0) + 1
    for stage in definition.stages:
        if (
            not stage.is_terminal
            and stage.gating_field is not None
            and gated_count[stage.gating_field] > 1
        ):
            raise fail(
                "field gated by more than one stage",
                {"worker_type": worker_type, "field": stage.gating_field},
            )

    for field in definition.fields:
        if gated_count.get(field.id, 0) == 0:
            raise fail(
                "declared field is never gated",
                {"worker_type": worker_type, "field": field.id},
            )

    if definition.worker_profile.specialist_skill not in known_skills:
        raise fail(
            "worker profile references an unknown skill",
            {
                "worker_type": worker_type,
                "specialist_skill": definition.worker_profile.specialist_skill,
            },
        )

    if definition.worker_profile.toolset_profile not in known_toolset_profiles:
        raise fail(
            "worker profile references an unknown toolset profile",
            {
                "worker_type": worker_type,
                "toolset_profile": definition.worker_profile.toolset_profile,
            },
        )

    require_conversation_backend_key(definition.worker_profile.default_backend)
    default_model = definition.worker_profile.default_model
    if not isinstance(default_model, str) or not default_model.strip():
        raise fail(
            "default_model must be a non-empty string",
            {"worker_type": worker_type, "default_model": default_model},
        )
    default_reasoning_effort = definition.worker_profile.default_reasoning_effort
    if default_reasoning_effort is not None and (
        not isinstance(default_reasoning_effort, str) or not default_reasoning_effort.strip()
    ):
        raise fail(
            "default_reasoning_effort must be null or a non-empty string",
            {"worker_type": worker_type, "default_reasoning_effort": default_reasoning_effort},
        )

class WorkerTypeRegistry:
    __slots__ = ("_definitions",)

    def __init__(
        self,
        definitions: Iterable[WorkerTypeDefinition],
        *,
        known_skills: frozenset[str],
        known_toolset_profiles: frozenset[str],
    ) -> None:
        table: dict[str, WorkerTypeDefinition] = {}
        for definition in definitions:
            if definition.worker_type in table:
                raise PlannerError(
                    ErrorCode.validation,
                    "duplicate worker type id",
                    {"worker_type": definition.worker_type},
                )
            validate_definition(
                definition,
                known_skills=known_skills,
                known_toolset_profiles=known_toolset_profiles,
            )
            table[definition.worker_type] = definition
        self._definitions = MappingProxyType(dict(table))

    def registered_worker_types(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def require(self, worker_type: str) -> WorkerTypeDefinition:
        try:
            return self._definitions[worker_type]
        except KeyError as exc:
            raise PlannerError(
                ErrorCode.not_found,
                "unknown worker type",
                {"worker_type": worker_type},
            ) from exc

    def manifest(self, worker_type: str) -> WorkerTypeManifest:
        definition = self.require(worker_type)
        stages: list[WorkerTypeManifestStage] = [
            {
                "id": stage.id,
                "label": stage.label,
                "gating_field": stage.gating_field,
                "is_terminal": stage.is_terminal,
                "ownership_mode": (
                    stage.ownership_mode.value
                    if stage.ownership_mode is not None
                    else None
                ),
            }
            for stage in definition.stages
        ]
        fields: list[WorkerTypeManifestField] = [
            {"id": field.id, "label": field.label} for field in definition.fields
        ]
        advance: dict[str, str] = {}
        for stage in definition.stage_ids():
            target = definition.advance_target(stage)
            if target is not None:
                advance[stage] = target
        return {
            "worker_type": definition.worker_type,
            "label": definition.label,
            "stages": stages,
            "advance": advance,
            "fields": fields,
            "ceiling_range": list(definition.ceiling_range()),
            "default_ceiling": definition.default_ceiling(),
            "worker_profile_id": definition.worker_profile.specialist_skill,
            "default_backend": (definition.worker_profile.default_backend),
            "default_model": definition.worker_profile.default_model,
            "default_reasoning_effort": (definition.worker_profile.default_reasoning_effort),
        }
