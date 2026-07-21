"""Production composition and the explicit Worker-type registry test seam."""

from __future__ import annotations

from dataclasses import dataclass

from planner.conversation.backend_catalog import (
    EmployeeBackendCatalog,
    build_production_employee_backend_catalog,
)
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.exploration import EXPLORATION_WORKER_TYPE_DEFINITION
from planner.worker_types.initiative_planning import INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION
from planner.worker_types.registry import WorkerTypeRegistry

_KNOWN_SKILLS = frozenset(
    {
        "panels-worker",
        "panels-worker-coding",
        "panels-worker-new-worker",
        "panels-worker-exploration",
        "panels-worker-initiative-planning",
    }
)
_KNOWN_TOOLSET_PROFILES = frozenset({"default"})
_PRODUCTION_WORKER_TYPE_DEFINITIONS = (
    CODING_WORKER_TYPE_DEFINITION,
    NEW_WORKER_TYPE_DEFINITION,
    EXPLORATION_WORKER_TYPE_DEFINITION,
    INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION,
)


@dataclass(frozen=True, slots=True)
class ConfiguredEmployeeRuntimeDefinitions:
    employee_backend_catalog: EmployeeBackendCatalog
    worker_type_registry: WorkerTypeRegistry

    def __post_init__(self) -> None:
        if self.worker_type_registry.employee_backend_catalog is not self.employee_backend_catalog:
            raise ValueError(
                "employee backend catalog and Worker-type registry must be the same authority"
            )


def build_employee_runtime_definitions(
    employee_backend_catalog: EmployeeBackendCatalog,
    *,
    worker_type_definitions: tuple[WorkerTypeDefinition, ...] = (
        _PRODUCTION_WORKER_TYPE_DEFINITIONS
    ),
    known_skills: frozenset[str] = _KNOWN_SKILLS,
    known_toolset_profiles: frozenset[str] = _KNOWN_TOOLSET_PROFILES,
) -> ConfiguredEmployeeRuntimeDefinitions:
    registry = WorkerTypeRegistry(
        worker_type_definitions,
        known_skills=known_skills,
        known_toolset_profiles=known_toolset_profiles,
        employee_backend_catalog=employee_backend_catalog,
    )
    return ConfiguredEmployeeRuntimeDefinitions(employee_backend_catalog, registry)


PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS = build_employee_runtime_definitions(
    build_production_employee_backend_catalog()
)
PRODUCTION_WORKER_TYPE_REGISTRY = PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.worker_type_registry

_configured_employee_runtime_definitions = PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS


def configured_employee_runtime_definitions() -> ConfiguredEmployeeRuntimeDefinitions:
    return _configured_employee_runtime_definitions


def configured_worker_type_registry() -> WorkerTypeRegistry:
    return _configured_employee_runtime_definitions.worker_type_registry


def install_employee_runtime_definitions_for_test(
    definitions: ConfiguredEmployeeRuntimeDefinitions,
) -> ConfiguredEmployeeRuntimeDefinitions:
    global _configured_employee_runtime_definitions
    previous = _configured_employee_runtime_definitions
    _configured_employee_runtime_definitions = definitions
    return previous


def restore_employee_runtime_definitions_for_test(
    definitions: ConfiguredEmployeeRuntimeDefinitions,
) -> None:
    global _configured_employee_runtime_definitions
    _configured_employee_runtime_definitions = definitions
