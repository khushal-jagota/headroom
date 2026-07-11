"""The Registry facade and its constructor build_registry.

build_registry runs the validator over every definition AT CONSTRUCTION and
enforces cross-definition type_id uniqueness — so an invalid registry cannot be
built; it refuses to boot. The reference catalogs (known specialist-skill ids,
known toolset-profile ids) are INJECTED, never imported: the composition root
passes the real catalogs at startup, tests pass them explicitly.

The Registry forwards to the pure views so callers never touch the internal
tables. ``require`` raises ``ErrorCode.not_found`` for an unknown type_id; every
validation violation raises ``ErrorCode.validation``.

Imports the two leaf modules (via logic) plus core/contracts only."""

from __future__ import annotations

from collections.abc import Iterable

from planner.core.contracts import ErrorCode, PlannerError
from planner.ticket_types.contracts import ManifestDict, Stage, WorkflowDefinition
from planner.ticket_types.logic import views
from planner.ticket_types.logic.manifest import serialize_definition
from planner.ticket_types.logic.validation import validate_definition


class Registry:
    """The single authority for ticket-type workflow definitions.

    Construction is the ONLY validating door: __init__ runs the full validator over
    every definition (per-definition validate_definition + cross-type_id uniqueness)
    and stores a defensive copy of the table. An invalid or duplicated definition
    raises here — so an invalid registry cannot be constructed through the public API,
    not merely through the build_registry factory. The retained table is a copy, so
    mutating the caller's original iterable/dict afterward cannot corrupt it."""

    def __init__(
        self,
        definitions: Iterable[WorkflowDefinition],
        *,
        known_skills: frozenset[str],
        known_toolset_profiles: frozenset[str],
    ) -> None:
        table: dict[str, WorkflowDefinition] = {}
        for defn in definitions:
            if defn.type_id in table:
                raise PlannerError(
                    ErrorCode.validation, "duplicate ticket type id", {"type_id": defn.type_id}
                )
            validate_definition(
                defn,
                known_skills=known_skills,
                known_toolset_profiles=known_toolset_profiles,
            )
            table[defn.type_id] = defn
        self._definitions = dict(table)   # defensive copy

    def type_ids(self) -> tuple[str, ...]:
        return tuple(self._definitions.keys())

    def require(self, type_id: str) -> WorkflowDefinition:
        """The definition for a type id; raises not_found if unknown."""
        try:
            return self._definitions[type_id]
        except KeyError as exc:
            raise PlannerError(
                ErrorCode.not_found, "unknown ticket type", {"type_id": type_id}
            ) from exc

    def definition_for(self, type_id: str) -> WorkflowDefinition:
        """Alias of require, for readability at call sites."""
        return self.require(type_id)

    def manifest(self, type_id: str) -> ManifestDict:
        return serialize_definition(self.require(type_id))

    # --- thin forwarders to the pure views -----------------------------------

    def stage_ids(self, type_id: str) -> tuple[str, ...]:
        return views.stage_ids(self.require(type_id))

    def state_index(self, type_id: str, state_id: str) -> int:
        return views.state_index(self.require(type_id), state_id)

    def require_stage(self, type_id: str, state_id: str) -> Stage:
        return views.require_stage(self.require(type_id), state_id)

    def is_terminal(self, type_id: str, state_id: str) -> bool:
        return views.is_terminal(self.require(type_id), state_id)

    def gating_field(self, type_id: str, state_id: str) -> str | None:
        return views.gating_field(self.require(type_id), state_id)

    def gate_map(self, type_id: str) -> dict[str, str]:
        return views.gate_map(self.require(type_id))

    def gated_state(self, type_id: str, field_id: str) -> str:
        return views.gated_state(self.require(type_id), field_id)

    def has_field(self, type_id: str, field_id: str) -> bool:
        return views.has_field(self.require(type_id), field_id)

    def field_ids(self, type_id: str) -> tuple[str, ...]:
        return views.field_ids(self.require(type_id))

    def advance_target(self, type_id: str, state_id: str) -> str | None:
        return views.advance_target(self.require(type_id), state_id)

    def advance_map(self, type_id: str) -> dict[str, str]:
        return views.advance_map(self.require(type_id))

    def ceiling_range(self, type_id: str) -> tuple[str, ...]:
        return views.ceiling_range(self.require(type_id))

    def default_ceiling(self, type_id: str) -> str:
        return views.default_ceiling(self.require(type_id))

    def linear_terminal_stage_id(self, type_id: str) -> str:
        return views.linear_terminal_stage_id(self.require(type_id))

    def transition_effect(
        self, type_id: str, implementer: str, old_state: str, new_state: str
    ) -> str | None:
        return views.transition_effect(self.require(type_id), implementer, old_state, new_state)


def build_registry(
    definitions: Iterable[WorkflowDefinition],
    *,
    known_skills: frozenset[str],
    known_toolset_profiles: frozenset[str],
) -> Registry:
    """Thin factory: assemble a validated Registry. All validation (per-definition
    rules + cross-type_id uniqueness) happens in Registry.__init__, so an invalid or
    duplicated definition raises here just as it does through the public constructor."""
    return Registry(
        definitions,
        known_skills=known_skills,
        known_toolset_profiles=known_toolset_profiles,
    )
