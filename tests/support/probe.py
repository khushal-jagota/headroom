"""The canonical test-only probe Worker type used to prove generic behavior."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import (
    ConfiguredWorkerRuntimeDefinitions,
    install_worker_runtime_definitions_for_test,
    restore_worker_runtime_definitions_for_test,
)
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.debugging import DEBUGGING_WORKER_TYPE_DEFINITION
from planner.worker_types.exploration import EXPLORATION_WORKER_TYPE_DEFINITION
from planner.worker_types.general import GENERAL_WORKER_TYPE_DEFINITION
from planner.worker_types.initiative_planning import INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION
from planner.worker_types.planning_day import PLANNING_DAY_WORKER_TYPE_DEFINITION
from planner.worker_types.planning_midday_check import (
    PLANNING_MIDDAY_CHECK_WORKER_TYPE_DEFINITION,
)
from planner.worker_types.planning_sprint import PLANNING_SPRINT_WORKER_TYPE_DEFINITION
from planner.worker_types.product_design import PRODUCT_DESIGN_WORKER_TYPE_DEFINITION
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
        StageDefinition(NEEDS_ALPHA, "Alpha", FIELD_ALPHA, False, StageOwnershipMode.worker),
        StageDefinition(NEEDS_BETA, "Beta", FIELD_BETA, False, StageOwnershipMode.user),
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
        default_model="probe-model",
        default_reasoning_effort="probe-high",
        toolset_profile="default",
        # A real backend key, and deliberately not one any shipped Worker type names:
        # the probe exists to prove a Worker type may run on a backend of its own.
        default_backend="hermes",
    ),
    supports_prefix_reconciliation=True,
)

PROBE_KNOWN_SKILLS: frozenset[str] = frozenset(
    {
        "panels-worker",
        "panels-worker-coding",
        "panels-worker-general",
        "panels-worker-debugging",
        "panels-worker-new-worker",
        "panels-worker-exploration",
        "panels-worker-initiative-planning",
        "panels-worker-product-design",
        "panels-worker-planning-day",
        "panels-worker-planning-midday-check",
        "panels-worker-planning-sprint",
        PROBE_SPECIALIST_SKILL,
    }
)
PROBE_KNOWN_TOOLSET_PROFILES: frozenset[str] = frozenset({"default"})


def build_probe_registry() -> WorkerTypeRegistry:
    return WorkerTypeRegistry(
        (
            CODING_WORKER_TYPE_DEFINITION,
            GENERAL_WORKER_TYPE_DEFINITION,
            DEBUGGING_WORKER_TYPE_DEFINITION,
            NEW_WORKER_TYPE_DEFINITION,
            EXPLORATION_WORKER_TYPE_DEFINITION,
            INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION,
            PRODUCT_DESIGN_WORKER_TYPE_DEFINITION,
            PLANNING_DAY_WORKER_TYPE_DEFINITION,
            PLANNING_MIDDAY_CHECK_WORKER_TYPE_DEFINITION,
            PLANNING_SPRINT_WORKER_TYPE_DEFINITION,
            PROBE_WORKER_TYPE_DEFINITION,
        ),
        known_skills=PROBE_KNOWN_SKILLS,
        known_toolset_profiles=PROBE_KNOWN_TOOLSET_PROFILES,
    )


_installed_definitions: ConfiguredWorkerRuntimeDefinitions | None = None


def install_probe_registry() -> WorkerTypeDefinition:
    global _installed_definitions
    registry = build_probe_registry()
    _installed_definitions = install_worker_runtime_definitions_for_test(
        ConfiguredWorkerRuntimeDefinitions(registry)
    )
    return registry.require("probe")


def uninstall_probe_registry() -> None:
    global _installed_definitions
    if _installed_definitions is None:
        raise RuntimeError("probe runtime definitions were not installed")
    restore_worker_runtime_definitions_for_test(_installed_definitions)
    _installed_definitions = None
