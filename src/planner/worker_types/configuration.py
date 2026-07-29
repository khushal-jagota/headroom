"""Production composition and the explicit Worker-type registry test seam."""

from __future__ import annotations

from dataclasses import dataclass

from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.debugging import DEBUGGING_WORKER_TYPE_DEFINITION
from planner.worker_types.exploration import EXPLORATION_WORKER_TYPE_DEFINITION
from planner.worker_types.initiative_planning import INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION
from planner.worker_types.personal import PERSONAL_TASK_WORKER_TYPE_DEFINITION
from planner.worker_types.planning_day import PLANNING_DAY_WORKER_TYPE_DEFINITION
from planner.worker_types.planning_midday_check import (
    PLANNING_MIDDAY_CHECK_WORKER_TYPE_DEFINITION,
)
from planner.worker_types.planning_sprint import PLANNING_SPRINT_WORKER_TYPE_DEFINITION
from planner.worker_types.product_design import PRODUCT_DESIGN_WORKER_TYPE_DEFINITION
from planner.worker_types.registry import WorkerTypeRegistry

_KNOWN_SKILLS = frozenset(
    {
        "panels-worker",
        "panels-worker-coding",
        "panels-worker-debugging",
        "panels-worker-new-worker",
        "panels-worker-exploration",
        "panels-worker-initiative-planning",
        "panels-worker-product-design",
        "panels-worker-planning-day",
        "panels-worker-planning-midday-check",
        "panels-worker-planning-sprint",
        "panels-worker-personal-task",
    }
)
_KNOWN_TOOLSET_PROFILES = frozenset({"default"})
_PRODUCTION_WORKER_TYPE_DEFINITIONS = (
    CODING_WORKER_TYPE_DEFINITION,
    DEBUGGING_WORKER_TYPE_DEFINITION,
    NEW_WORKER_TYPE_DEFINITION,
    EXPLORATION_WORKER_TYPE_DEFINITION,
    INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION,
    PRODUCT_DESIGN_WORKER_TYPE_DEFINITION,
    PLANNING_DAY_WORKER_TYPE_DEFINITION,
    PLANNING_MIDDAY_CHECK_WORKER_TYPE_DEFINITION,
    PLANNING_SPRINT_WORKER_TYPE_DEFINITION,
    PERSONAL_TASK_WORKER_TYPE_DEFINITION,
)


@dataclass(frozen=True, slots=True)
class ConfiguredWorkerRuntimeDefinitions:
    worker_type_registry: WorkerTypeRegistry


def build_worker_runtime_definitions(
    *,
    worker_type_definitions: tuple[WorkerTypeDefinition, ...] = (
        _PRODUCTION_WORKER_TYPE_DEFINITIONS
    ),
    known_skills: frozenset[str] = _KNOWN_SKILLS,
    known_toolset_profiles: frozenset[str] = _KNOWN_TOOLSET_PROFILES,
) -> ConfiguredWorkerRuntimeDefinitions:
    registry = WorkerTypeRegistry(
        worker_type_definitions,
        known_skills=known_skills,
        known_toolset_profiles=known_toolset_profiles,
    )
    return ConfiguredWorkerRuntimeDefinitions(registry)


PRODUCTION_WORKER_RUNTIME_DEFINITIONS = build_worker_runtime_definitions()
PRODUCTION_WORKER_TYPE_REGISTRY = PRODUCTION_WORKER_RUNTIME_DEFINITIONS.worker_type_registry

_configured_worker_runtime_definitions = PRODUCTION_WORKER_RUNTIME_DEFINITIONS


def configured_worker_runtime_definitions() -> ConfiguredWorkerRuntimeDefinitions:
    return _configured_worker_runtime_definitions


def configured_worker_type_registry() -> WorkerTypeRegistry:
    return _configured_worker_runtime_definitions.worker_type_registry


def install_worker_runtime_definitions_for_test(
    definitions: ConfiguredWorkerRuntimeDefinitions,
) -> ConfiguredWorkerRuntimeDefinitions:
    global _configured_worker_runtime_definitions
    previous = _configured_worker_runtime_definitions
    _configured_worker_runtime_definitions = definitions
    return previous


def restore_worker_runtime_definitions_for_test(
    definitions: ConfiguredWorkerRuntimeDefinitions,
) -> None:
    global _configured_worker_runtime_definitions
    _configured_worker_runtime_definitions = definitions
