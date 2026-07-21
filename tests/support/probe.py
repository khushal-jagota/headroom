"""The canonical test-only probe Worker type used to prove generic behavior."""

from __future__ import annotations

from typing import Never

from planner.conversation.backend_catalog import (
    EmployeeBackendBuildContext,
    EmployeeBackendCatalog,
    EmployeeBackendRegistration,
)
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import (
    ConfiguredEmployeeRuntimeDefinitions,
    install_employee_runtime_definitions_for_test,
    restore_employee_runtime_definitions_for_test,
)
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.exploration import EXPLORATION_WORKER_TYPE_DEFINITION
from planner.worker_types.initiative_planning import INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION
from planner.worker_types.registry import WorkerTypeRegistry

NEEDS_ALPHA = "".join(("needs_", "alpha"))
NEEDS_BETA = "".join(("needs_", "beta"))
FIELD_ALPHA = "".join(("al", "pha"))
FIELD_BETA = "".join(("be", "ta"))

PROBE_SPECIALIST_SKILL = "probe-worker"
PROBE_FIELD_IDS: tuple[str, ...] = ("kickoff", FIELD_ALPHA, FIELD_BETA)

PROBE_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="probe",
    label="Probe",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(NEEDS_ALPHA, "Alpha", FIELD_ALPHA, False, StageOwnershipMode.user),
        StageDefinition(NEEDS_BETA, "Beta", FIELD_BETA, False, StageOwnershipMode.paired),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition(FIELD_ALPHA, "Alpha"),
        FieldDefinition(FIELD_BETA, "Beta"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill=PROBE_SPECIALIST_SKILL,
        model=None,
        reasoning_effort=None,
        toolset_profile="default",
        default_employee_backend="probe-backend",
    ),
    supports_prefix_reconciliation=True,
)

PROBE_KNOWN_SKILLS: frozenset[str] = frozenset(
    {
        "panels-worker",
        "panels-worker-coding",
        "panels-worker-new-worker",
        "panels-worker-exploration",
        "panels-worker-initiative-planning",
        PROBE_SPECIALIST_SKILL,
    }
)
PROBE_KNOWN_TOOLSET_PROFILES: frozenset[str] = frozenset({"default"})


def _unmaterialized_probe_backend(
    _context: EmployeeBackendBuildContext,
) -> Never:
    raise RuntimeError("unit-only probe backend was materialized")


PROBE_EMPLOYEE_BACKEND_CATALOG = EmployeeBackendCatalog(
    (
        EmployeeBackendRegistration("hermes", _unmaterialized_probe_backend),
        EmployeeBackendRegistration("probe-backend", _unmaterialized_probe_backend),
    )
)


def build_probe_registry(
    employee_backend_catalog: EmployeeBackendCatalog = PROBE_EMPLOYEE_BACKEND_CATALOG,
) -> WorkerTypeRegistry:
    return WorkerTypeRegistry(
        (
            CODING_WORKER_TYPE_DEFINITION,
            NEW_WORKER_TYPE_DEFINITION,
            EXPLORATION_WORKER_TYPE_DEFINITION,
            INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION,
            PROBE_WORKER_TYPE_DEFINITION,
        ),
        known_skills=PROBE_KNOWN_SKILLS,
        known_toolset_profiles=PROBE_KNOWN_TOOLSET_PROFILES,
        employee_backend_catalog=employee_backend_catalog,
    )


_installed_definitions: ConfiguredEmployeeRuntimeDefinitions | None = None


def install_probe_registry() -> WorkerTypeDefinition:
    global _installed_definitions
    registry = build_probe_registry()
    _installed_definitions = install_employee_runtime_definitions_for_test(
        ConfiguredEmployeeRuntimeDefinitions(
            PROBE_EMPLOYEE_BACKEND_CATALOG,
            registry,
        )
    )
    return registry.require("probe")


def uninstall_probe_registry() -> None:
    global _installed_definitions
    if _installed_definitions is None:
        raise RuntimeError("probe runtime definitions were not installed")
    restore_employee_runtime_definitions_for_test(_installed_definitions)
    _installed_definitions = None
