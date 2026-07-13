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
    "default_ceiling",
    "field_ids",
    "has_field",
    "registry",
    "require",
    "set_registry_for_test",
    "views",
]

# The reference catalogs the registry validator needs (R14/R15), matching
# coding's WorkerProfile (specialist_skill="panels-worker-coding", toolset_profile="default").
# "panels-worker" stays: it is the base role loaded via HERMES_TUI_SKILLS and a real
# shipped skill; the catalog is the set of skills the registry may reference.
# Inlined here — NOT imported from minds/config (D102 no-cycle seam).
_KNOWN_SKILLS: frozenset[str] = frozenset({"panels-worker", "panels-worker-coding"})
_KNOWN_TOOLSET_PROFILES: frozenset[str] = frozenset({"default"})

_registry: Registry | None = None

# A test-installed registry that supersedes the production ``coding``-only one for
# ``registry()`` / ``require()`` / ``default_ceiling()``. Production never sets it;
# it exists so a test can resolve a SECOND type through the DB/validation doors
# (t_tt02x) while ``coding_registry()`` / ``coding_definition()`` stay coding-only.
_active_registry: Registry | None = None


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


def registry() -> Registry:
    """The registry the persistence doors resolve each row's type through — the
    test-installed one when present, else the production ``coding``-only singleton.

    This is the single swap point: every door and the startup audit resolve types
    via this (and its ``require``/``default_ceiling`` passthroughs), so a test can
    install a registry carrying a second type WITHOUT any door naming
    ``ticket_types`` (the F6 import boundary stays intact)."""
    return _active_registry if _active_registry is not None else coding_registry()


def require(type_id: str) -> WorkflowDefinition:
    """Resolve a type id through :func:`registry`; raises ``not_found`` if unknown."""
    return registry().require(type_id)


def default_ceiling(type_id: str) -> str:
    """The per-type default ceiling (first entry of the type's ceiling range)."""
    return registry().default_ceiling(type_id)


def set_registry_for_test(reg: Registry | None) -> None:
    """Install (or clear with ``None``) a registry that supersedes the production
    ``coding``-only one for :func:`registry`. Test-only seam for driving a second
    type through the persistence doors."""
    global _active_registry
    _active_registry = reg


def field_ids(definition: WorkflowDefinition) -> tuple[str, ...]:
    """Every declared field id of the definition, in order (thin wrapper so the
    codec reaches the views without importing ``ticket_types``)."""
    return views.field_ids(definition)


def has_field(definition: WorkflowDefinition, field_id: str) -> bool:
    """Whether ``field_id`` is a declared field of the definition (thin wrapper)."""
    return views.has_field(definition, field_id)
