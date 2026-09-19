"""The Worker types this process is running, loaded from the database it opened.

Nearly a hundred places ask what a Worker type is, most of them far from a connection,
so the answer is held here for the process rather than passed down to every caller. It is
loaded when a database is opened and again whenever a type is written, which are the only
two moments it can change.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.registry import KNOWN_TOOLSET_PROFILES, WorkerTypeRegistry


@dataclass(frozen=True, slots=True)
class ConfiguredWorkerRuntimeDefinitions:
    worker_type_registry: WorkerTypeRegistry


def build_worker_runtime_definitions(
    *,
    worker_type_definitions: tuple[WorkerTypeDefinition, ...],
    known_skills: frozenset[str],
    known_toolset_profiles: frozenset[str] = KNOWN_TOOLSET_PROFILES,
) -> ConfiguredWorkerRuntimeDefinitions:
    registry = WorkerTypeRegistry(
        worker_type_definitions,
        known_skills=known_skills,
        known_toolset_profiles=known_toolset_profiles,
    )
    return ConfiguredWorkerRuntimeDefinitions(registry)


_EMPTY_DEFINITIONS = ConfiguredWorkerRuntimeDefinitions(
    WorkerTypeRegistry((), known_skills=frozenset(), known_toolset_profiles=frozenset())
)
_GUARD = threading.Lock()
_configured_worker_runtime_definitions = _EMPTY_DEFINITIONS


def load_worker_runtime_definitions(conn: sqlite3.Connection) -> ConfiguredWorkerRuntimeDefinitions:
    """Read the Worker types out of this database and make them the ones in force."""
    # Imported here because the store reads the registry's rules, and this module is what
    # the rest of the application asks for a registry.
    from planner.managed_skills import skill_names
    from planner.worker_types.store import read_definitions, worker_types_table_exists

    if not worker_types_table_exists(conn):
        return configured_worker_runtime_definitions()
    definitions = build_worker_runtime_definitions(
        worker_type_definitions=read_definitions(conn),
        known_skills=skill_names(conn),
    )
    with _GUARD:
        global _configured_worker_runtime_definitions
        _configured_worker_runtime_definitions = definitions
    return definitions


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
