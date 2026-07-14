"""Tests for the ticket-type registry (t_tt00). Contracts-only, additive.

Every assertion is a concrete value: negative tests assert the COMPLETE
PlannerError (exact code + message + full detail dict); parity tests assert the
coding definition's derived views EQUAL the live constants imported from
planner.tickets.contracts / .logic.machine, so drift on either side fails here.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from planner.core.contracts import ErrorCode, PlannerError
from planner.minds import config as minds_config
from planner.minds.config import repo_root
from planner.ticket_types.coding import CODING_DEFINITION
from planner.ticket_types.contracts import (
    FieldDef,
    Stage,
    TransitionHook,
    WorkflowDefinition,
)
from planner.ticket_types.logic import validation, views
from planner.ticket_types.registry import Registry, build_registry
from planner.tickets.contracts import (
    CODING_EMPLOYEE_STAGE_ORDER,
    CODING_GATING_FIELD_BY_STAGE,
    CODING_NEXT_STAGE_BY_STAGE,
    CODING_STAGE_ORDER,
    CodingStage,
    FieldName,
    Implementer,
    TicketStatus,
)
from planner.tickets.logic import coding_bridge, machine
from planner.tickets.logic.machine import FIELD_GATES

KNOWN_SKILLS = frozenset({"panels-worker", "panels-worker-coding"})  # coding's base + specialist
KNOWN_TOOLSET_PROFILES = frozenset({"default"})        # BRIEF §9.2


def build() -> Registry:
    return build_registry(
        [CODING_DEFINITION],
        known_skills=KNOWN_SKILLS,
        known_toolset_profiles=KNOWN_TOOLSET_PROFILES,
    )


def validate(defn: WorkflowDefinition) -> None:
    validation.validate_definition(
        defn, known_skills=KNOWN_SKILLS, known_toolset_profiles=KNOWN_TOOLSET_PROFILES
    )


def assert_raises_planner(fn, *, code: ErrorCode, message: str, detail: dict) -> None:
    with pytest.raises(PlannerError) as ei:
        fn()
    assert ei.value.code == code
    assert ei.value.message == message
    assert ei.value.detail == detail          # complete dict, not a subset


# =====================================================================
# Acceptance item 1 — startup validation + one isolated negative per rule
# =====================================================================


def test_build_registry_with_only_coding_succeeds() -> None:
    assert build().type_ids() == ("coding",)
    assert build().require("coding").type_id == "coding"


def test_require_unknown_type_raises_not_found() -> None:
    assert_raises_planner(
        lambda: build().require("nope"),
        code=ErrorCode.not_found,
        message="unknown worker type",
        detail={"worker_type": "nope"},
    )


def test_public_constructor_validates_invalid_definition() -> None:  # F2
    # The invariant "an invalid registry cannot be constructed" holds through the PUBLIC
    # Registry constructor, not only through the build_registry factory.
    bad = dataclasses.replace(CODING_DEFINITION, stages=())
    assert_raises_planner(
        lambda: Registry(
            [bad], known_skills=KNOWN_SKILLS, known_toolset_profiles=KNOWN_TOOLSET_PROFILES
        ),
        code=ErrorCode.validation,
        message="definition has no stages",
        detail={"worker_type": "coding"},
    )


def test_public_constructor_rejects_duplicate_type_id() -> None:  # F2
    assert_raises_planner(
        lambda: Registry(
            [CODING_DEFINITION, CODING_DEFINITION],
            known_skills=KNOWN_SKILLS,
            known_toolset_profiles=KNOWN_TOOLSET_PROFILES,
        ),
        code=ErrorCode.validation,
        message="duplicate worker type id",
        detail={"worker_type": "coding"},
    )


def test_registry_table_is_defensively_copied() -> None:  # F2
    # The constructor builds its table from the iterable and stores a copy, so mutating
    # the caller's original list after construction cannot change the registry.
    defs = [CODING_DEFINITION]
    reg = Registry(
        defs, known_skills=KNOWN_SKILLS, known_toolset_profiles=KNOWN_TOOLSET_PROFILES
    )
    defs.clear()
    assert reg.type_ids() == ("coding",)


def test_empty_stages_rejected() -> None:  # R0
    defn = dataclasses.replace(CODING_DEFINITION, stages=())
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="definition has no stages",
        detail={"worker_type": "coding"},
    )


def test_empty_fields_rejected() -> None:  # R0
    defn = dataclasses.replace(CODING_DEFINITION, fields=())
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="definition has no fields",
        detail={"worker_type": "coding"},
    )


def test_duplicate_type_id_rejected() -> None:  # R1
    assert_raises_planner(
        lambda: build_registry(
            [CODING_DEFINITION, CODING_DEFINITION],
            known_skills=KNOWN_SKILLS,
            known_toolset_profiles=KNOWN_TOOLSET_PROFILES,
        ),
        code=ErrorCode.validation,
        message="duplicate worker type id",
        detail={"worker_type": "coding"},
    )


def test_duplicate_stage_id_rejected() -> None:  # R2
    stages = tuple(CODING_DEFINITION.stages)
    # Two stages share id="needs_success"; put the duplicate right after the real one.
    dup = Stage(id="needs_success", label="Success", gating_field="success", is_terminal=False)
    new_stages = stages[:2] + (dup,) + stages[2:]
    defn = dataclasses.replace(CODING_DEFINITION, stages=new_stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="duplicate stage id",
        detail={"worker_type": "coding", "stage": "needs_success"},
    )


def test_duplicate_field_id_rejected() -> None:  # R3
    fields = tuple(CODING_DEFINITION.fields)
    dup = FieldDef(id="success", label="Success")
    new_fields = fields[:2] + (dup,) + fields[2:]
    defn = dataclasses.replace(CODING_DEFINITION, fields=new_fields)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="duplicate field id",
        detail={"worker_type": "coding", "field": "success"},
    )


def test_first_stage_not_kickoff_rejected() -> None:  # R4
    # Drop the leading kickoff so stages[0].id == "needs_success".
    defn = dataclasses.replace(CODING_DEFINITION, stages=CODING_DEFINITION.stages[1:])
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="first stage must be needs_kickoff",
        detail={"worker_type": "coding", "first": "needs_success"},
    )


def test_nonterminal_without_successor_rejected() -> None:  # R6 (isolated)
    # Final stage keeps id="done" but is_terminal=False -> no terminal at all -> R6 first.
    stages = CODING_DEFINITION.stages[:-1] + (
        Stage(id="done", label="Done", gating_field=None, is_terminal=False),
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="linear order must have exactly one terminal",
        detail={"worker_type": "coding", "stage": "done"},
    )


def test_wrong_terminal_id_rejected() -> None:  # R5 (isolated)
    # Exactly one terminal, and it is last (R6 passes); its id is wrong -> R5.
    stages = CODING_DEFINITION.stages[:-1] + (
        Stage(id="finished", label="Finished", gating_field=None, is_terminal=True),
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="last stage must be done",
        detail={"worker_type": "coding", "last": "finished"},
    )


def test_dropped_used_as_linear_stage_rejected() -> None:  # R7
    # Insert a dropped stage into the linear order (mid-order, before done).
    stages = CODING_DEFINITION.stages[:-1] + (
        Stage(id="dropped", label="Dropped", gating_field="x", is_terminal=False),
        CODING_DEFINITION.stages[-1],
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="dropped may not be a linear stage",
        detail={"worker_type": "coding", "stage": "dropped"},
    )


def test_terminal_stage_gating_field_rejected() -> None:  # R8
    stages = CODING_DEFINITION.stages[:-1] + (
        Stage(id="done", label="Done", gating_field="closeout", is_terminal=True),
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="terminal stage may not gate a field",
        detail={"worker_type": "coding", "stage": "done"},
    )


def test_missing_gate_for_nonterminal_rejected() -> None:  # R9
    stages = tuple(
        dataclasses.replace(s, gating_field=None) if s.id == "needs_success" else s
        for s in CODING_DEFINITION.stages
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="non-terminal stage must gate a field",
        detail={"worker_type": "coding", "stage": "needs_success"},
    )


def test_gate_points_at_undeclared_field_rejected() -> None:  # R10
    stages = tuple(
        dataclasses.replace(s, gating_field="ghost") if s.id == "needs_success" else s
        for s in CODING_DEFINITION.stages
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="gating field references an undeclared field",
        detail={"worker_type": "coding", "stage": "needs_success", "gating_field": "ghost"},
    )


def test_field_gated_by_two_stages_rejected() -> None:  # R11
    # Add a stage that also gates "success" (already gated by needs_success). "success"
    # is declared (R10 passes) and still gated (R12 passes); R11 is the first failure.
    stages = (
        CODING_DEFINITION.stages[:2]
        + (Stage(id="needs_extra", label="Extra", gating_field="success", is_terminal=False),)
        + CODING_DEFINITION.stages[2:]
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="field gated by more than one stage",
        detail={"worker_type": "coding", "field": "success"},
    )


def test_declared_field_never_gated_rejected() -> None:  # R12
    fields = CODING_DEFINITION.fields + (FieldDef(id="extra", label="Extra"),)
    defn = dataclasses.replace(CODING_DEFINITION, fields=fields)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="declared field is never gated",
        detail={"worker_type": "coding", "field": "extra"},
    )


def test_first_field_not_kickoff_rejected() -> None:  # R13
    fields = CODING_DEFINITION.fields[1:] + (CODING_DEFINITION.fields[0],)
    defn = dataclasses.replace(CODING_DEFINITION, fields=fields)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="first field must be kickoff",
        detail={"worker_type": "coding", "first_field": "success"},
    )


def test_worker_profile_unknown_skill_rejected() -> None:  # R14
    profile = dataclasses.replace(CODING_DEFINITION.worker_profile, specialist_skill="not-a-skill")
    defn = dataclasses.replace(CODING_DEFINITION, worker_profile=profile)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="worker profile references an unknown skill",
        detail={"worker_type": "coding", "specialist_skill": "not-a-skill"},
    )


def test_worker_profile_unknown_toolset_rejected() -> None:  # R15
    profile = dataclasses.replace(CODING_DEFINITION.worker_profile, toolset_profile="nope")
    defn = dataclasses.replace(CODING_DEFINITION, worker_profile=profile)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="worker profile references an unknown toolset profile",
        detail={"worker_type": "coding", "toolset_profile": "nope"},
    )


def test_hook_unknown_stage_rejected() -> None:  # R16
    hooks = (
        TransitionHook(
            old_stage="ghost",
            new_stage="needs_implementation",
            implementer="khushal",
            effect="user_takeover",
        ),
    )
    defn = dataclasses.replace(CODING_DEFINITION, transition_hooks=hooks)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="transition hook references an unknown stage",
        detail={"worker_type": "coding", "stage": "ghost"},
    )


def test_hook_unknown_implementer_rejected() -> None:  # R17
    hooks = (
        TransitionHook(
            old_stage="needs_plan",
            new_stage="needs_implementation",
            implementer="bob",
            effect="user_takeover",
        ),
    )
    defn = dataclasses.replace(CODING_DEFINITION, transition_hooks=hooks)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="transition hook references an unknown implementer",
        detail={"worker_type": "coding", "implementer": "bob"},
    )


def test_hook_unknown_effect_rejected() -> None:  # R18
    hooks = (
        TransitionHook(
            old_stage="needs_plan",
            new_stage="needs_implementation",
            implementer="khushal",
            effect="explode",
        ),
    )
    defn = dataclasses.replace(CODING_DEFINITION, transition_hooks=hooks)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="transition hook references an unknown effect",
        detail={"worker_type": "coding", "effect": "explode"},
    )


def test_duplicate_hook_key_rejected() -> None:  # R19
    hook = CODING_DEFINITION.transition_hooks[0]
    defn = dataclasses.replace(CODING_DEFINITION, transition_hooks=(hook, hook))
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="duplicate transition hook",
        detail={
            "worker_type": "coding",
            "key": ["needs_plan", "needs_implementation", "khushal"],
        },
    )


def test_non_bool_is_terminal_rejected() -> None:  # F3 — strict bool identity in R6
    # Final stage with is_terminal=1 (a non-bool truthy). Bool-identity classification
    # sees NO terminal, so R6 rejects deterministically before any non-bool reaches the
    # manifest. (Type is str for the dataclass field, so tell the type-checker to ignore.)
    stages = CODING_DEFINITION.stages[:-1] + (
        Stage(id="done", label="Done", gating_field=None, is_terminal=1),  # type: ignore[arg-type]
    )
    defn = dataclasses.replace(CODING_DEFINITION, stages=stages)
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="linear order must have exactly one terminal",
        detail={"worker_type": "coding", "stage": "done"},
    )


def test_non_bool_supports_prefix_reconciliation_rejected() -> None:  # F3 (R20)
    defn = dataclasses.replace(
        CODING_DEFINITION,
        supports_prefix_reconciliation="yes",  # type: ignore[arg-type]
    )
    assert_raises_planner(
        lambda: validate(defn),
        code=ErrorCode.validation,
        message="supports_prefix_reconciliation must be a bool",
        detail={"worker_type": "coding"},
    )


# =====================================================================
# Acceptance item 2 — parity golden test vs live constants
# =====================================================================


def test_coding_stage_order_equals_state_order() -> None:
    assert views.stage_ids(CODING_DEFINITION) == tuple(s.value for s in CODING_STAGE_ORDER)


def test_coding_gate_map_equals_gating_field() -> None:
    assert views.gate_map(CODING_DEFINITION) == {
        s.value: f.value for s, f in CODING_GATING_FIELD_BY_STAGE.items()
    }


def test_coding_advance_map_equals_advance_target() -> None:
    assert views.advance_map(CODING_DEFINITION) == {
        s.value: t.value for s, t in CODING_NEXT_STAGE_BY_STAGE.items()
    }


def test_coding_field_order_equals_fieldname() -> None:
    assert views.field_ids(CODING_DEFINITION) == tuple(f.value for f in FieldName)


def test_coding_field_order_equals_field_name_order() -> None:  # F4
    # t_tt02b: TicketFields is now a generic slot map (no six named dataclass fields),
    # so the coding field-order invariant is pinned against the FieldName vocabulary in
    # declared order — the coding field ids the definition is built from — not against
    # TicketFields' internal shape.
    assert views.field_ids(CODING_DEFINITION) == tuple(f.value for f in FieldName)


def test_coding_field_gates_inverse_equals_field_gates() -> None:  # F4
    assert {f.value: s.value for f, s in FIELD_GATES.items()} == {
        field_id: views.gated_stage(CODING_DEFINITION, field_id)
        for field_id in views.field_ids(CODING_DEFINITION)
    }


def test_coding_ceiling_range_leads_with_kickoff_then_worker_state_order() -> None:
    # The ceiling range is now the FULL linear order: leading needs_kickoff (a fresh
    # ticket's default/selectable ceiling), then the worker states.
    assert views.ceiling_range(CODING_DEFINITION) == (
        "needs_kickoff",
    ) + tuple(s.value for s in CODING_EMPLOYEE_STAGE_ORDER)


def test_coding_default_ceiling_is_needs_kickoff() -> None:
    # Default ceiling is now the leading needs_kickoff (a fresh ticket starts scoped to
    # kickoff); the FIRST WORKER stage stays needs_success and is a distinct concept.
    assert views.default_ceiling(CODING_DEFINITION) == "needs_kickoff"
    assert views.first_worker_stage(CODING_DEFINITION) == "needs_success"
    assert views.first_worker_stage(CODING_DEFINITION) == CODING_EMPLOYEE_STAGE_ORDER[0].value


def test_coding_linear_terminal_stage_id_is_done() -> None:
    assert views.linear_terminal_stage_id(CODING_DEFINITION) == CodingStage.done.value


# =====================================================================
# Acceptance item 2b — derived API behavior incl. unknown-stage safety (F2)
# =====================================================================


def test_stage_index_matches_state_order() -> None:
    for s in CODING_STAGE_ORDER:
        assert views.stage_index(CODING_DEFINITION, s.value) == CODING_STAGE_ORDER.index(s)


def test_stage_index_unknown_raises() -> None:
    assert_raises_planner(
        lambda: views.stage_index(CODING_DEFINITION, "ghost"),
        code=ErrorCode.validation,
        message="stage outside the linear order",
        detail={"stage": "ghost"},
    )


def test_is_terminal_done_and_dropped_true() -> None:
    assert views.is_terminal(CODING_DEFINITION, "done") is True
    assert views.is_terminal(CODING_DEFINITION, "dropped") is True
    assert views.is_terminal(CODING_DEFINITION, "needs_plan") is False


def test_is_terminal_unknown_raises() -> None:
    assert_raises_planner(
        lambda: views.is_terminal(CODING_DEFINITION, "ghost"),
        code=ErrorCode.validation,
        message="stage outside the linear order",
        detail={"stage": "ghost"},
    )


def test_gating_field_terminal_is_none_unknown_raises() -> None:
    # Both known terminals return None (parity with machine.gating_field(dropped) == None);
    # only an UNKNOWN id like "ghost" raises. (F1: dropped must not be misread as unknown.)
    assert views.gating_field(CODING_DEFINITION, "done") is None
    assert views.gating_field(CODING_DEFINITION, "dropped") is None
    assert_raises_planner(
        lambda: views.gating_field(CODING_DEFINITION, "ghost"),
        code=ErrorCode.validation,
        message="stage outside the linear order",
        detail={"stage": "ghost"},
    )


def test_advance_target_terminal_is_none_unknown_raises() -> None:
    assert views.advance_target(CODING_DEFINITION, "done") is None
    assert views.advance_target(CODING_DEFINITION, "dropped") is None  # F1
    assert_raises_planner(
        lambda: views.advance_target(CODING_DEFINITION, "ghost"),
        code=ErrorCode.validation,
        message="stage outside the linear order",
        detail={"stage": "ghost"},
    )


def test_gated_stage_inverse() -> None:
    assert views.gated_stage(CODING_DEFINITION, "success") == "needs_success"
    assert views.gated_stage(CODING_DEFINITION, "kickoff") == "needs_kickoff"


def test_gated_stage_ungated_field_raises() -> None:
    assert_raises_planner(
        lambda: views.gated_stage(CODING_DEFINITION, "ghost"),
        code=ErrorCode.validation,
        message="field gates no stage",
        detail={"field": "ghost"},
    )


def test_has_field() -> None:
    assert views.has_field(CODING_DEFINITION, "plan") is True
    assert views.has_field(CODING_DEFINITION, "ghost") is False


def test_require_stage_returns_stage() -> None:
    assert views.require_stage(CODING_DEFINITION, "needs_plan").label == "Plan"
    assert_raises_planner(
        lambda: views.require_stage(CODING_DEFINITION, "ghost"),
        code=ErrorCode.validation,
        message="stage outside the linear order",
        detail={"stage": "ghost"},
    )


def test_transition_effect_matches_plan_handoff() -> None:
    assert (
        views.transition_effect(
            CODING_DEFINITION, "khushal", "needs_plan", "needs_implementation"
        )
        == "user_takeover"
    )
    assert (
        views.transition_effect(
            CODING_DEFINITION, "panels_worker", "needs_plan", "needs_implementation"
        )
        is None
    )
    assert (
        views.transition_effect(
            CODING_DEFINITION, "khushal", "needs_success", "needs_approach"
        )
        is None
    )
    # Equals the live machine hook, without modifying plan_handoff_status.
    live = machine.plan_handoff_status(
        Implementer.khushal, CodingStage.needs_plan, CodingStage.needs_implementation
    )
    assert live == TicketStatus.user_takeover
    assert live.value == "user_takeover"


# =====================================================================
# Acceptance item 3 — manifest exact shape
# =====================================================================

EXPECTED = {
    "worker_type": "coding",
    "label": "Coding",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
        },
        {
            "id": "needs_success",
            "label": "Success",
            "gating_field": "success",
            "is_terminal": False,
        },
        {
            "id": "needs_approach",
            "label": "Approach",
            "gating_field": "approach",
            "is_terminal": False,
        },
        {"id": "needs_plan", "label": "Plan", "gating_field": "plan", "is_terminal": False},
        {
            "id": "needs_implementation",
            "label": "Implementation",
            "gating_field": "implementation",
            "is_terminal": False,
        },
        {
            "id": "needs_closeout",
            "label": "Closeout",
            "gating_field": "closeout",
            "is_terminal": False,
        },
        {"id": "done", "label": "Done", "gating_field": None, "is_terminal": True},
    ],
    "dropped": {"id": "dropped", "label": "Dropped", "gating_field": None, "is_terminal": True},
    "advance": {
        "needs_kickoff": "needs_success",
        "needs_success": "needs_approach",
        "needs_approach": "needs_plan",
        "needs_plan": "needs_implementation",
        "needs_implementation": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "success", "label": "Success"},
        {"id": "approach", "label": "Approach"},
        {"id": "plan", "label": "Plan"},
        {"id": "implementation", "label": "Implementation"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_success",
        "needs_approach",
        "needs_plan",
        "needs_implementation",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-coding",
}


def test_coding_manifest_exact() -> None:
    assert build().manifest("coding") == EXPECTED


def test_coding_manifest_json_roundtrips() -> None:
    assert json.loads(json.dumps(build().manifest("coding"))) == EXPECTED


def test_manifest_omits_prefix_reconciliation() -> None:  # F5
    assert "supports_prefix_reconciliation" not in build().manifest("coding")


def test_coding_supports_prefix_reconciliation() -> None:  # F5
    assert build().require("coding").supports_prefix_reconciliation is True


# =====================================================================
# t_tt05 — every registered type's specialist is shipped + provisioned + loadable
# =====================================================================


def test_every_specialist_skill_is_shipped_and_provisioned() -> None:
    # For every PRODUCTION-registered type (coding + new_worker), its declared worker
    # specialist must resolve to a shipped, loadable skill: SKILL.md exists under skills/
    # (Codex F6 — skill_view needs the file, not just the dir) AND the skill is in
    # PLANNER_SKILL_NAMES so provisioning symlinks it into the worker home. Iterated over
    # the production registry (not the probe fixture) — the truthful assertion is that
    # every SHIPPED specialist is shipped + provisioned.
    registry = coding_bridge.coding_registry()
    assert registry.type_ids() == ("coding", "new_worker")
    for type_id in registry.type_ids():
        specialist = registry.require(type_id).worker_profile.specialist_skill
        assert (repo_root() / "skills" / specialist / "SKILL.md").is_file(), specialist
        assert specialist in minds_config.PLANNER_SKILL_NAMES, specialist


# =====================================================================
# Acceptance item 4 — additive-only / no production consumer
# =====================================================================

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
_PLANNER_ROOT = _SRC_ROOT / "planner"
_TICKET_TYPES_ROOT = _PLANNER_ROOT / "ticket_types"
_ALLOWED_OUTBOUND = {"planner.tickets.contracts", "planner.core.contracts"}


def _anchor_package_for(path: Path) -> str:
    """The dotted package a relative import in `path` anchors to (Python's `__package__`).

    For a package __init__.py the anchor IS the package itself (planner.ticket_types);
    for a regular module the anchor is its CONTAINING package (planner.tickets.data ->
    planner.tickets). This distinction is exactly what the earlier resolver got wrong."""
    rel = path.relative_to(_SRC_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        return ".".join(parts[:-1])       # package __init__: anchor is the package
    return ".".join(parts[:-1])           # regular module: anchor is its containing package


def _resolve_relative(anchor_package: str, level: int, target: str | None) -> str:
    """Resolve a relative import against its anchor package (Python semantics): level 1
    is the anchor package itself; each extra level strips one more trailing component;
    a `target` (the name after `import`/the `x` in `from ..x`) is then appended."""
    pkg_parts = anchor_package.split(".") if anchor_package else []
    strip = level - 1                      # level 1 => no extra strip
    base = pkg_parts[: len(pkg_parts) - strip] if strip > 0 else pkg_parts
    resolved = base + target.split(".") if target else base
    return ".".join(resolved)


def test_ticket_types_outbound_imports_allowlisted() -> None:  # F3
    offending: set[str] = set()
    for path in _TICKET_TYPES_ROOT.rglob("*.py"):
        anchor = _anchor_package_for(path)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    offending |= _classify_outbound(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    resolved = _resolve_relative(anchor, node.level, node.module)
                    offending |= _classify_outbound(resolved)
                elif node.module is not None:
                    offending |= _classify_outbound(node.module)
    assert offending == set()


def _classify_outbound(module: str) -> set[str]:
    """A cross-package planner.* target is offending unless it is intra-package or allowlisted."""
    if not (module == "planner" or module.startswith("planner.")):
        return set()
    if module == "planner.ticket_types" or module.startswith("planner.ticket_types."):
        return set()  # intra-package
    if module in _ALLOWED_OUTBOUND:
        return set()
    return {module}


# t_tt01: the registry now has exactly one production importer — the designated
# seam. F6 is narrowed from "no production importer" to "exactly {coding_bridge}",
# which is stronger: it still fails on any second (scattered) importer AND fails if
# the seam itself stops importing ticket_types.
_ALLOWED_TICKET_TYPES_IMPORTER = _PLANNER_ROOT / "tickets" / "logic" / "coding_bridge.py"


def test_no_production_module_imports_ticket_types() -> None:  # F6 hardened matcher
    importers: set[Path] = set()
    for path in _PLANNER_ROOT.rglob("*.py"):
        if _TICKET_TYPES_ROOT in path.parents or path == _TICKET_TYPES_ROOT:
            continue
        anchor = _anchor_package_for(path)
        tree = ast.parse(path.read_text(), filename=str(path))
        if _file_imports_ticket_types(tree, anchor):
            importers.add(path)
    assert importers == {_ALLOWED_TICKET_TYPES_IMPORTER}


def _resolves_to_ticket_types(module: str) -> bool:
    return module == "planner.ticket_types" or module.startswith("planner.ticket_types.")


def _file_imports_ticket_types(tree: ast.AST, anchor_package: str) -> bool:
    """True if any import in `tree` (absolute or relative, resolved against
    `anchor_package`) targets planner.ticket_types[.*]."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _resolves_to_ticket_types(alias.name):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                resolved = _resolve_relative(anchor_package, node.level, node.module)
                if _resolves_to_ticket_types(resolved):
                    return True
            elif node.module is not None and _resolves_to_ticket_types(node.module):
                return True
            elif node.module == "planner":
                for alias in node.names:
                    if alias.name == "ticket_types":
                        return True
    return False


# --- F4 regression: the relative-import resolver + the two escapes it now catches ---


def test_anchor_package_for_init_vs_module() -> None:
    # A package __init__.py anchors to the package itself; a regular module anchors to
    # its containing package. This is the distinction the old resolver collapsed away.
    init_path = _PLANNER_ROOT / "tickets" / "__init__.py"
    module_path = _PLANNER_ROOT / "tickets" / "data.py"
    assert _anchor_package_for(init_path) == "planner.tickets"
    assert _anchor_package_for(module_path) == "planner.tickets"


def test_resolve_relative_semantics() -> None:
    # (anchor_package, level, module) -> resolved dotted name, matching CPython.
    cases = [
        # from planner/tickets/__init__.py:  from ..ticket_types import Registry
        ("planner.tickets", 2, "ticket_types", "planner.ticket_types"),
        # from planner/ticket_types/__init__.py:  from ..minds import SharedGateway
        ("planner.ticket_types", 2, "minds", "planner.minds"),
        # from planner/tickets/data.py:  from . import contracts  (anchor is planner.tickets)
        ("planner.tickets", 1, "contracts", "planner.tickets.contracts"),
        # from a package __init__:  from . import x  (level 1 => the package itself)
        ("planner.ticket_types", 1, "registry", "planner.ticket_types.registry"),
        # bare `from .. import y`
        ("planner.tickets", 2, None, "planner"),
    ]
    for anchor, level, module, expected in cases:
        assert _resolve_relative(anchor, level, module) == expected


def _import_from(level: int, module: str | None, *names: str) -> ast.Module:
    """A synthetic module whose sole statement is one relative ImportFrom."""
    stmt = ast.ImportFrom(
        module=module, names=[ast.alias(name=n, asname=None) for n in names], level=level
    )
    return ast.fix_missing_locations(ast.Module(body=[stmt], type_ignores=[]))


def test_relative_import_of_ticket_types_from_package_init_is_flagged() -> None:  # F4
    # `from ..ticket_types import Registry` written in planner/tickets/__init__.py must
    # be caught by the production-import guard (anchor = the package planner.tickets).
    tree = _import_from(2, "ticket_types", "Registry")
    assert _file_imports_ticket_types(tree, "planner.tickets") is True


def test_relative_outbound_from_ticket_types_init_is_flagged() -> None:  # F4
    # `from ..minds import SharedGateway` written in planner/ticket_types/__init__.py must
    # be classified as an offending outbound target (planner.minds), not allowlisted.
    node = _import_from(2, "minds", "SharedGateway").body[0]
    assert isinstance(node, ast.ImportFrom)
    resolved = _resolve_relative("planner.ticket_types", node.level, node.module)
    assert resolved == "planner.minds"
    assert _classify_outbound(resolved) == {"planner.minds"}
