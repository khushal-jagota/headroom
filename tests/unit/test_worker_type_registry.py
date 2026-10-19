from __future__ import annotations

from tests.support.probe import build_shipped_registry, shipped_definition

from planner.worker_types.contracts import (
    WorkerTypeDefinition,
)
from planner.worker_types.registry import WorkerTypeRegistry

SHIPPED_REGISTRY = build_shipped_registry()
CODING_WORKER_TYPE_DEFINITION = shipped_definition("coding")

KNOWN_SKILLS = frozenset({"panels-worker", "panels-worker-coding", "panels-worker-new-worker"})
KNOWN_TOOLSETS = frozenset({"default"})


def registry(*definitions: WorkerTypeDefinition) -> WorkerTypeRegistry:
    return WorkerTypeRegistry(
        definitions,
        known_skills=KNOWN_SKILLS,
        known_toolset_profiles=KNOWN_TOOLSETS,
    )
