"""Immutable Worker-type declarations and their behavior-bearing interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import StageOwnershipMode

# The three names the runtime fixes. Everything else a Worker type calls its Stages and
# fields is the type's own business. `consequences` is the field every Worker type must
# declare, because a Worker type ends by landing what it produced. `brief` is paired first
# for a type that opens with one.
CONSEQUENCES_FIELD_ID = "consequences"
BRIEF_FIELD_ID = "brief"
NEEDS_BRIEF_STAGE_ID = f"needs_{BRIEF_FIELD_ID}"


@dataclass(frozen=True, slots=True)
class StageDefinition:
    id: str
    label: str
    gating_field: str | None
    is_terminal: bool
    ownership_mode: StageOwnershipMode | None


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class WorkerProfile:
    """What a Worker type ships as: its skill, its toolset, and what it launches on.

    ``default_backend`` and ``default_model`` are named together because neither means
    anything without the other, and a Worker type that named no model would launch its
    workers on whatever its backend picked for itself — a value nobody chose and nobody
    can see. ``default_reasoning_effort`` may be absent: some models take none.
    """

    specialist_skill: str
    default_model: str
    default_reasoning_effort: str | None
    toolset_profile: str
    default_backend: str


@dataclass(frozen=True, slots=True)
class WorkerTypeDefinition:
    worker_type: str
    label: str
    stages: tuple[StageDefinition, ...]
    fields: tuple[FieldDefinition, ...]
    worker_profile: WorkerProfile

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
        return stage in self.stage_ids()

    def is_terminal(self, stage: str) -> bool:
        return self.stage_definition(stage).is_terminal

    def gating_field(self, stage: str) -> str | None:
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
        index = self.stage_index(stage)
        if self.stages[index].is_terminal:
            return None
        return self.stages[index + 1].id

    def ceiling_range(self) -> tuple[str, ...]:
        return self.stage_ids()

    def resolve_ceiling(self, raw: str) -> str:
        """Accept a stage id or the plain name of the field that stage gates."""
        if raw in self.ceiling_range():
            return raw
        try:
            return self.stage_gated_by(raw)
        except PlannerError as exc:
            raise PlannerError(
                ErrorCode.scope_invalid, "unknown ceiling", {"ceiling": raw}
            ) from exc

    def default_ceiling(self) -> str:
        return self.ceiling_range()[0]

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

class WorkerTypeManifestStage(TypedDict):
    id: str
    label: str
    gating_field: str | None
    is_terminal: bool
    ownership_mode: str | None


class WorkerTypeManifestField(TypedDict):
    id: str
    label: str


class WorkerTypeManifest(TypedDict):
    worker_type: str
    label: str
    stages: list[WorkerTypeManifestStage]
    advance: dict[str, str]
    fields: list[WorkerTypeManifestField]
    ceiling_range: list[str]
    default_ceiling: str
    worker_profile_id: str
    default_backend: str
    default_model: str
    default_reasoning_effort: str | None
