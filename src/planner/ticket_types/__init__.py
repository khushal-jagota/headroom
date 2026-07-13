"""The ticket-type registry: the single authority for ticket-type workflow
definitions.

Holds the shipped ``coding`` definition (reproducing today's landed lifecycle
exactly), validates and refuses to boot on violation, serves one serialized
manifest, and exposes the derived lookups callers ask for without exposing its
internal tables.

Nothing in production consumes this package yet (Phase 0 is additive-only)."""

from __future__ import annotations

from planner.ticket_types.coding import CODING_DEFINITION
from planner.ticket_types.contracts import (
    FieldDef,
    ManifestDict,
    ManifestField,
    ManifestStage,
    Stage,
    TransitionHook,
    WorkerProfile,
    WorkflowDefinition,
)
from planner.ticket_types.logic import serialize_definition, validate_definition
from planner.ticket_types.new_worker import NEW_WORKER_DEFINITION
from planner.ticket_types.registry import Registry, build_registry

__all__ = [
    "CODING_DEFINITION",
    "NEW_WORKER_DEFINITION",
    "FieldDef",
    "ManifestDict",
    "ManifestField",
    "ManifestStage",
    "Registry",
    "Stage",
    "TransitionHook",
    "WorkerProfile",
    "WorkflowDefinition",
    "build_registry",
    "serialize_definition",
    "validate_definition",
]
