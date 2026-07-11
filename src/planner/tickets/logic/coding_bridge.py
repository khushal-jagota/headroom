"""The single production seam onto the ticket-type registry (t_tt01).

This is the ONE production module that imports ``planner.ticket_types``; every
other engine module reaches the registry's derived views transitively through it
(the F6 guard test asserts the importer set is exactly ``{coding_bridge}``).

It builds a process-wide singleton ``Registry`` lazily on first use, with the
reference catalogs INJECTED as inline literals here — never imported from
``minds/config`` (the D102 no-cycle seam rule). Construction validates every
definition, so a malformed ``coding`` definition refuses to boot exactly as the
registry contract requires.

``coding_definition()`` is the compatibility DEFAULT: a parameterized engine op
called with ``definition=None`` resolves ``coding`` through it, while a caller
that passes an explicit definition drives a different workflow through the same
code (the seam t_tt02 extends).

Imports ``planner.ticket_types`` (the package facade) plus the pure views leaf.
No cycle: ``ticket_types`` reaches only ``tickets.contracts`` / ``core.contracts``
(both sinks), never ``tickets.logic``.
"""

from __future__ import annotations

from planner.ticket_types import CODING_DEFINITION, Registry, WorkflowDefinition, build_registry
from planner.ticket_types.logic import views as _views

# The engine reaches the derived views exclusively through this re-export, so no
# engine module other than coding_bridge names ``ticket_types`` in an import.
views = _views

# WorkflowDefinition is re-exported so engine modules annotate against it via
# ``coding_bridge.WorkflowDefinition`` without importing ``ticket_types`` themselves.
__all__ = [
    "WorkflowDefinition",
    "coding_definition",
    "coding_registry",
    "field_ids",
    "has_field",
    "views",
]

# The reference catalogs the registry validator needs (R14/R15), matching
# coding's WorkerProfile (specialist_skill="panels-worker", toolset_profile="default").
# Inlined here — NOT imported from minds/config (D102 no-cycle seam).
_KNOWN_SKILLS: frozenset[str] = frozenset({"panels-worker"})
_KNOWN_TOOLSET_PROFILES: frozenset[str] = frozenset({"default"})

_registry: Registry | None = None


def coding_registry() -> Registry:
    """The process-wide singleton registry, built and validated once per process.

    Validation runs at build, so a broken definition raises here — the registry
    refuses to boot rather than failing on first ticket op."""
    global _registry
    if _registry is None:
        _registry = build_registry(
            [CODING_DEFINITION],
            known_skills=_KNOWN_SKILLS,
            known_toolset_profiles=_KNOWN_TOOLSET_PROFILES,
        )
    return _registry


def coding_definition() -> WorkflowDefinition:
    """The shipped ``coding`` workflow definition — the compatibility default a
    parameterized engine op resolves when ``definition`` is omitted."""
    return coding_registry().require("coding")


def field_ids(definition: WorkflowDefinition) -> tuple[str, ...]:
    """Every declared field id of the definition, in order (thin wrapper so the
    codec reaches the views without importing ``ticket_types``)."""
    return views.field_ids(definition)


def has_field(definition: WorkflowDefinition, field_id: str) -> bool:
    """Whether ``field_id`` is a declared field of the definition (thin wrapper)."""
    return views.has_field(definition, field_id)
