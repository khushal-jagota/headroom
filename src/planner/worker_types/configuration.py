"""Production composition and the explicit Worker-type registry test seam."""

from __future__ import annotations

from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.exploration import EXPLORATION_WORKER_TYPE_DEFINITION
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION
from planner.worker_types.registry import WorkerTypeRegistry

_KNOWN_SKILLS = frozenset(
    {
        "panels-worker",
        "panels-worker-coding",
        "panels-worker-new-worker",
        "panels-worker-exploration",
    }
)
_KNOWN_TOOLSET_PROFILES = frozenset({"default"})
_PRODUCTION_WORKER_TYPE_DEFINITIONS = (
    CODING_WORKER_TYPE_DEFINITION,
    NEW_WORKER_TYPE_DEFINITION,
    EXPLORATION_WORKER_TYPE_DEFINITION,
)

PRODUCTION_WORKER_TYPE_REGISTRY = WorkerTypeRegistry(
    _PRODUCTION_WORKER_TYPE_DEFINITIONS,
    known_skills=_KNOWN_SKILLS,
    known_toolset_profiles=_KNOWN_TOOLSET_PROFILES,
)

_configured_worker_type_registry = PRODUCTION_WORKER_TYPE_REGISTRY


def configured_worker_type_registry() -> WorkerTypeRegistry:
    return _configured_worker_type_registry


def install_worker_type_registry_for_test(registry: WorkerTypeRegistry) -> None:
    global _configured_worker_type_registry
    _configured_worker_type_registry = registry


def restore_production_worker_type_registry_for_test() -> None:
    global _configured_worker_type_registry
    _configured_worker_type_registry = PRODUCTION_WORKER_TYPE_REGISTRY
