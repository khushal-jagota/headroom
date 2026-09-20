"""The canonical test-only probe Worker type used to prove generic behavior."""

from __future__ import annotations

import json
import sqlite3

from planner.core.migrations.versions.settled_stage_and_field_ids import renamed_definition
from planner.core.migrations.versions.worker_types_in_database import SHIPPED_WORKER_TYPES
from planner.tickets.contracts import StageOwnershipMode
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
from planner.worker_types.registry import WorkerTypeRegistry
from planner.worker_types.store import definition_from_json, write_definition

NEEDS_ALPHA = "".join(("needs_", "alpha"))
NEEDS_BETA = "".join(("needs_", "beta"))
FIELD_ALPHA = "".join(("al", "pha"))
FIELD_BETA = "".join(("be", "ta"))

# The rule requires the field named ``consequences``. It says nothing about the Stage id,
# so the probe gives that Stage a name of its own, like every other Stage here.
NEEDS_LANDING = "".join(("needs_", "landing"))

PROBE_SPECIALIST_SKILL = "probe-worker"

PROBE_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="probe",
    label="Probe",
    stages=(
        StageDefinition("needs_brief", "Brief", "brief", False, StageOwnershipMode.worker),
        StageDefinition(NEEDS_ALPHA, "Alpha", FIELD_ALPHA, False, StageOwnershipMode.worker),
        StageDefinition(NEEDS_BETA, "Beta", FIELD_BETA, False, StageOwnershipMode.user),
        StageDefinition(NEEDS_LANDING, "Landing", "consequences", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    fields=(
        FieldDefinition("brief", "Brief"),
        FieldDefinition(FIELD_ALPHA, "Alpha"),
        FieldDefinition(FIELD_BETA, "Beta"),
        FieldDefinition("consequences", "Consequences"),
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
)

# The migration carries a frozen copy of what shipped, so it still names the `dropped`
# stage the one_ticket_ending migration removes and still spells the ids the
# settled_stage_and_field_ids migration moves. A stored record has been through both.
SHIPPED_DEFINITIONS: tuple[WorkerTypeDefinition, ...] = tuple(
    definition_from_json(
        json.dumps(
            renamed_definition({k: v for k, v in shipped.items() if k != "dropped"})
        )
    )
    for shipped in SHIPPED_WORKER_TYPES
)
PROBE_KNOWN_SKILLS: frozenset[str] = frozenset(
    {definition.worker_profile.specialist_skill for definition in SHIPPED_DEFINITIONS}
    | {"panels-worker", PROBE_SPECIALIST_SKILL}
)
PROBE_KNOWN_TOOLSET_PROFILES: frozenset[str] = frozenset({"default"})


def build_probe_registry() -> WorkerTypeRegistry:
    """The Worker types a database is seeded with, plus the probe."""
    return WorkerTypeRegistry(
        SHIPPED_DEFINITIONS + (PROBE_WORKER_TYPE_DEFINITION,),
        known_skills=PROBE_KNOWN_SKILLS,
        known_toolset_profiles=PROBE_KNOWN_TOOLSET_PROFILES,
    )


def build_shipped_registry() -> WorkerTypeRegistry:
    """The Worker types a database is seeded with, exactly as it is seeded with them."""
    return WorkerTypeRegistry(
        SHIPPED_DEFINITIONS,
        known_skills=PROBE_KNOWN_SKILLS,
        known_toolset_profiles=PROBE_KNOWN_TOOLSET_PROFILES,
    )


def seed_probe_worker_type(conn: sqlite3.Connection, *, now: int = 0) -> WorkerTypeDefinition:
    """Store the probe Worker type through the door production writes through."""
    conn.execute(
        "INSERT OR IGNORE INTO managed_skills (skill_name, source_text, updated_at) "
        "VALUES (?, ?, ?)",
        (
            PROBE_SPECIALIST_SKILL,
            f"---\nname: {PROBE_SPECIALIST_SKILL}\ndescription: The probe worker.\n---\n\nBody.\n",
            now,
        ),
    )
    write_definition(conn, PROBE_WORKER_TYPE_DEFINITION, now=now)
    return PROBE_WORKER_TYPE_DEFINITION


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


def shipped_definition(worker_type: str) -> WorkerTypeDefinition:
    """One of the Worker types a database is seeded with, by id."""
    for definition in SHIPPED_DEFINITIONS:
        if definition.worker_type == worker_type:
            return definition
    raise KeyError(worker_type)
