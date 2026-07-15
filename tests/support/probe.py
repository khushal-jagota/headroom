"""The canonical test-only probe Worker type used to prove generic behavior."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import (
    install_worker_type_registry_for_test,
    restore_production_worker_type_registry_for_test,
)
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.exploration import EXPLORATION_WORKER_TYPE_DEFINITION
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
    ),
    supports_prefix_reconciliation=True,
)

PROBE_KNOWN_SKILLS: frozenset[str] = frozenset(
    {
        "panels-worker",
        "panels-worker-coding",
        "panels-worker-new-worker",
        "panels-worker-exploration",
        PROBE_SPECIALIST_SKILL,
    }
)
PROBE_KNOWN_TOOLSET_PROFILES: frozenset[str] = frozenset({"default"})


def build_probe_registry() -> WorkerTypeRegistry:
    return WorkerTypeRegistry(
        (
            CODING_WORKER_TYPE_DEFINITION,
            NEW_WORKER_TYPE_DEFINITION,
            EXPLORATION_WORKER_TYPE_DEFINITION,
            PROBE_WORKER_TYPE_DEFINITION,
        ),
        known_skills=PROBE_KNOWN_SKILLS,
        known_toolset_profiles=PROBE_KNOWN_TOOLSET_PROFILES,
    )


def install_probe_registry() -> WorkerTypeDefinition:
    registry = build_probe_registry()
    install_worker_type_registry_for_test(registry)
    return registry.require("probe")


def uninstall_probe_registry() -> None:
    restore_production_worker_type_registry_for_test()
