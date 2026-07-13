"""The registry validator: validate_definition raises PlannerError on the first
violation, checking rules R0–R19 in a FIXED order so a deterministic first
failure is guaranteed.

The reference catalogs — known specialist-skill ids and known toolset-profile
ids — are INJECTED as parameters, never imported, so this module stays a pure
leaf. Only Implementer and TicketStatus value sets are read from the
``tickets/contracts`` leaf enum (the sole allowed cross-package import besides
the core error contract).

Every violation raises ``ErrorCode.validation`` (distinguished by message +
detail); ``ErrorCode.not_found`` is reserved for Registry.require on an unknown
type. Pure: contracts + core error contract only."""

from __future__ import annotations

from planner.core.contracts import ErrorCode, JsonDict, PlannerError
from planner.ticket_types.contracts import WorkflowDefinition
from planner.tickets.contracts import Implementer, TicketStatus


def validate_definition(
    defn: WorkflowDefinition,
    *,
    known_skills: frozenset[str],
    known_toolset_profiles: frozenset[str],
) -> None:
    """Raise PlannerError on the first rule violated, checking R0 (non-emptiness)
    before any indexing of stages[0]/stages[-1]/ceiling_range[0]."""
    type_id = defn.type_id

    def fail(message: str, detail: JsonDict) -> PlannerError:
        return PlannerError(ErrorCode.validation, message, detail)

    # R0 — non-empty (before any indexing).
    if len(defn.stages) < 1:
        raise fail("definition has no stages", {"type_id": type_id})
    if len(defn.fields) < 1:
        raise fail("definition has no fields", {"type_id": type_id})

    # R2 — unique stage ids (R1 is cross-definition, enforced in build_registry).
    seen_states: set[str] = set()
    for stage in defn.stages:
        if stage.id in seen_states:
            raise fail("duplicate stage id", {"type_id": type_id, "state": stage.id})
        seen_states.add(stage.id)

    # R3 — unique field ids.
    seen_fields: set[str] = set()
    for field_def in defn.fields:
        if field_def.id in seen_fields:
            raise fail("duplicate field id", {"type_id": type_id, "field": field_def.id})
        seen_fields.add(field_def.id)

    first = defn.stages[0]
    last = defn.stages[-1]

    # R4 — first stage is needs_kickoff, gating kickoff.
    if first.id != "needs_kickoff" or first.gating_field != "kickoff":
        raise fail("first stage must be needs_kickoff", {"type_id": type_id, "first": first.id})

    # R6 — exactly one linear terminal, and it is the last stage. Bool identity (is
    # True), not truthiness, so a stage with is_terminal=1 is NOT read as terminal and
    # cannot smuggle a non-bool into the manifest.
    terminals = [s for s in defn.stages if s.is_terminal is True]
    if len(terminals) != 1 or terminals[0] is not last:
        # offending id: the first stray terminal, else the last stage (no terminal at all).
        offending = terminals[0].id if terminals else last.id
        raise fail(
            "linear order must have exactly one terminal",
            {"type_id": type_id, "state": offending},
        )

    # R5 — the last (sole terminal) stage is done.
    if last.id != "done":
        raise fail("last stage must be done", {"type_id": type_id, "last": last.id})

    # R7 — dropped reserved / not linear; done only as the last stage; dropped_stage well-formed.
    for stage in defn.stages:
        if stage.id == "dropped":
            raise fail(
                "dropped may not be a linear stage", {"type_id": type_id, "state": "dropped"}
            )
    for stage in defn.stages[:-1]:
        if stage.id == "done":
            raise fail("done may not be a mid stage", {"type_id": type_id, "state": "done"})
    dropped = defn.dropped_stage
    if dropped.id != "dropped" or dropped.is_terminal is not True:
        raise fail("dropped may not be a linear stage", {"type_id": type_id, "state": "dropped"})

    # R8 — terminals carry no gate (done, then dropped).
    if last.gating_field is not None:
        raise fail("terminal stage may not gate a field", {"type_id": type_id, "state": "done"})
    if dropped.gating_field is not None:
        raise fail("terminal stage may not gate a field", {"type_id": type_id, "state": "dropped"})

    # R9 — every non-terminal stage gates a field.
    for stage in defn.stages:
        if not stage.is_terminal and stage.gating_field is None:
            raise fail(
                "non-terminal stage must gate a field", {"type_id": type_id, "state": stage.id}
            )

    # R10 — every non-terminal gate references a declared field.
    declared_field_ids = {f.id for f in defn.fields}
    for stage in defn.stages:
        if not stage.is_terminal and stage.gating_field not in declared_field_ids:
            raise fail(
                "gating field references an undeclared field",
                {"type_id": type_id, "state": stage.id, "gating_field": stage.gating_field},
            )

    # R11 — no field gated by two stages.
    gated_count: dict[str, int] = {}
    for stage in defn.stages:
        if not stage.is_terminal and stage.gating_field is not None:
            gated_count[stage.gating_field] = gated_count.get(stage.gating_field, 0) + 1
    for stage in defn.stages:
        if not stage.is_terminal and stage.gating_field is not None:
            if gated_count[stage.gating_field] > 1:
                raise fail(
                    "field gated by more than one stage",
                    {"type_id": type_id, "field": stage.gating_field},
                )

    # R12 — every declared field is gated exactly once.
    for field_def in defn.fields:
        if gated_count.get(field_def.id, 0) == 0:
            raise fail("declared field is never gated", {"type_id": type_id, "field": field_def.id})

    # R13 — first field is kickoff.
    if defn.fields[0].id != "kickoff":
        raise fail(
            "first field must be kickoff", {"type_id": type_id, "first_field": defn.fields[0].id}
        )

    # R14 — worker-profile skill reference.
    if defn.worker_profile.specialist_skill not in known_skills:
        raise fail(
            "worker profile references an unknown skill",
            {"type_id": type_id, "specialist_skill": defn.worker_profile.specialist_skill},
        )

    # R15 — worker-profile toolset reference.
    if defn.worker_profile.toolset_profile not in known_toolset_profiles:
        raise fail(
            "worker profile references an unknown toolset profile",
            {"type_id": type_id, "toolset_profile": defn.worker_profile.toolset_profile},
        )

    # R16 — hook stage references (old_state and new_state are known linear stage ids).
    known_stage_ids = {s.id for s in defn.stages}
    for hook in defn.transition_hooks:
        for state_id in (hook.old_state, hook.new_state):
            if state_id not in known_stage_ids:
                raise fail(
                    "transition hook references an unknown stage",
                    {"type_id": type_id, "state": state_id},
                )

    # R17 — hook implementer reference.
    known_implementers = {i.value for i in Implementer}
    for hook in defn.transition_hooks:
        if hook.implementer not in known_implementers:
            raise fail(
                "transition hook references an unknown implementer",
                {"type_id": type_id, "implementer": hook.implementer},
            )

    # R18 — hook effect reference.
    known_effects = {s.value for s in TicketStatus}
    for hook in defn.transition_hooks:
        if hook.effect not in known_effects:
            raise fail(
                "transition hook references an unknown effect",
                {"type_id": type_id, "effect": hook.effect},
            )

    # R19 — no duplicate hook key.
    seen_keys: set[tuple[str, str, str]] = set()
    for hook in defn.transition_hooks:
        key = (hook.old_state, hook.new_state, hook.implementer)
        if key in seen_keys:
            raise fail(
                "duplicate transition hook",
                {"type_id": type_id, "key": [hook.old_state, hook.new_state, hook.implementer]},
            )
        seen_keys.add(key)

    # R20 — supports_prefix_reconciliation is a real bool. `bool` is an `int` subclass,
    # so an exact-type check keeps 1 / "yes" out (they would otherwise pass a truthiness
    # or isinstance-int test and serialize a non-bool downstream).
    if type(defn.supports_prefix_reconciliation) is not bool:
        raise fail(
            "supports_prefix_reconciliation must be a bool",
            {"type_id": type_id},
        )
