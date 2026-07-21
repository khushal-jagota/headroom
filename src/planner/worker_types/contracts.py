"""Immutable Worker-type declarations and their behavior-bearing interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import StageOwnershipMode


@dataclass(frozen=True, slots=True)
class StageDefinition:
    id: str
    label: str
    gating_field: str | None
    is_terminal: bool
    default_ownership_mode: StageOwnershipMode | None


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class WorkerProfile:
    specialist_skill: str
    model: str | None
    reasoning_effort: str | None
    toolset_profile: str
    default_employee_backend: str


@dataclass(frozen=True, slots=True)
class WorkerTypeDefinition:
    worker_type: str
    label: str
    stages: tuple[StageDefinition, ...]
    dropped_stage: StageDefinition
    fields: tuple[FieldDefinition, ...]
    worker_profile: WorkerProfile
    supports_prefix_reconciliation: bool

    def stage_ids(self) -> tuple[str, ...]:
        return tuple(stage.id for stage in self.stages)

    def stage_index(self, stage: str) -> int:
        try:
            return self.stage_ids().index(stage)
        except ValueError as exc:
            raise PlannerError(
                ErrorCode.validation,
                "stage outside the linear order",
                {"stage": stage},
            ) from exc

    def stage_definition(self, stage: str) -> StageDefinition:
        for stage_definition in self.stages:
            if stage_definition.id == stage:
                return stage_definition
        raise PlannerError(
            ErrorCode.validation,
            "stage outside the linear order",
            {"stage": stage},
        )

    def is_known_stage(self, stage: str) -> bool:
        return stage == self.dropped_stage.id or stage in self.stage_ids()

    def is_terminal(self, stage: str) -> bool:
        if stage == self.dropped_stage.id:
            return True
        return self.stage_definition(stage).is_terminal

    def gating_field(self, stage: str) -> str | None:
        if stage == self.dropped_stage.id:
            return None
        return self.stage_definition(stage).gating_field

    def stage_gated_by(self, field: str) -> str:
        for stage_definition in self.stages:
            if stage_definition.gating_field == field:
                return stage_definition.id
        raise PlannerError(
            ErrorCode.validation,
            "field gates no stage",
            {"field": field},
        )

    def field_ids(self) -> tuple[str, ...]:
        return tuple(field.id for field in self.fields)

    def has_field(self, field: str) -> bool:
        return field in self.field_ids()

    def field_definition(self, field: str) -> FieldDefinition:
        for field_definition in self.fields:
            if field_definition.id == field:
                return field_definition
        raise PlannerError(
            ErrorCode.validation,
            "unknown ticket field",
            {"field": field},
        )

    def advance_target(self, stage: str) -> str | None:
        if stage == self.dropped_stage.id:
            return None
        index = self.stage_index(stage)
        if self.stages[index].is_terminal:
            return None
        return self.stages[index + 1].id

    def ceiling_range(self) -> tuple[str, ...]:
        return self.stage_ids()

    def default_ceiling(self) -> str:
        return self.ceiling_range()[0]

    def first_worker_stage(self) -> str:
        return self.stage_ids()[1]

    def completed_stage(self) -> str:
        for stage_definition in self.stages:
            if stage_definition.is_terminal:
                return stage_definition.id
        raise PlannerError(
            ErrorCode.validation,
            "linear order has no terminal",
            {"worker_type": self.worker_type},
        )

    def validate_ticket_position(self, stage: str, ceiling: str) -> None:
        if not self.is_known_stage(stage):
            raise PlannerError(
                ErrorCode.validation,
                "stage outside the linear order",
                {"stage": stage},
            )
        if ceiling not in self.ceiling_range():
            raise PlannerError(
                ErrorCode.scope_invalid,
                "ceiling outside the type's range",
                {"worker_type": self.worker_type, "ceiling": ceiling},
            )

    def reconciliation_field_order(self) -> tuple[str, ...]:
        return tuple(
            stage.gating_field
            for stage in self.stages
            if not stage.is_terminal and stage.gating_field is not None
        )


class WorkerTypeManifestStage(TypedDict):
    id: str
    label: str
    gating_field: str | None
    is_terminal: bool
    default_ownership_mode: str | None


class WorkerTypeManifestField(TypedDict):
    id: str
    label: str


class WorkerTypeManifest(TypedDict):
    worker_type: str
    label: str
    stages: list[WorkerTypeManifestStage]
    dropped: WorkerTypeManifestStage
    advance: dict[str, str]
    fields: list[WorkerTypeManifestField]
    ceiling_range: list[str]
    default_ceiling: str
    worker_profile_id: str
    default_employee_backend: str
