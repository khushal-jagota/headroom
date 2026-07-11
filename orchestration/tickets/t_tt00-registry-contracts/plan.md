# t_tt00 — Implementation plan: ticket-type registry, `coding` definition, served manifest

Phase 0 of `orchestration/ticket-types-redesign/PLAN.md`. **Contracts-only, additive, zero behavior
change.** Build the registry deep module and register `coding` as the first workflow definition,
reproducing the now-landed lifecycle **exactly** (proven against the live constants, not hand-transcribed).
Nothing in production consumes the registry after this ticket; the existing `./verify` suite must pass
**unchanged**.

Anchors below are `file:line` against the tree as read for this plan. Re-confirm before editing.

> Codex plan review (`plan-review.txt`, verdict DONE_WITH_CONCERNS) folded in: F1 completed validator,
> F2 finished the t_tt01-facing derived API, F3 removed the `minds.config` dependency via catalog
> injection, F4 tightened parity, F5 added `supports_prefix_reconciliation`, F6 isolated negative tests
> + hardened the AST matcher. Orchestrator confirmed all four prior open questions in the BRIEF; §9 is
> now "resolved decisions."

---

## 1. Module layout

New first-class sibling domain, exactly as the BRIEF recommends and as `PRINCIPLES.md` §"Codebase
structure" prescribes (a non-trivial thing gets a folder; layers within a domain, never a flat pile):

```
src/planner/ticket_types/
    __init__.py          # re-exports the public surface (build_registry, Registry, error helpers)
    contracts.py         # registry data types (WorkflowDefinition, Stage, FieldDef, WorkerProfile, TransitionHook) + manifest TypedDicts
    logic/
        __init__.py      # pure-logic docstring, re-exports validate_definition + the derived-view helpers
        validation.py    # validate_definition(defn, *, known_skills, known_toolset_profiles) -> raises PlannerError (pure)
        views.py         # derived-view functions over a definition (state_index, gating_field, advance_target, …) (pure)
        manifest.py      # serialize_definition(defn) -> ManifestDict (pure)
    registry.py          # Registry facade + build_registry(definitions, *, known_skills, known_toolset_profiles); validates at construction
    coding.py            # the `coding` WorkflowDefinition literal, sourced from tickets/contracts enums
```

Rationale for the split:
- `contracts.py` holds only shapes (frozen dataclasses + TypedDicts), matching every other domain's
  `contracts.py` (`tickets/contracts.py`, `sprints/contracts.py:1`).
- `logic/` holds pure, dependency-free functions (validation, derived views, manifest) — `PRINCIPLES.md`
  §"Pure logic": unit-testable with no mocks. Mirrors `tickets/logic/` and `sprints/logic/`.
- `registry.py` is the stateful assembly point: `build_registry(...)` constructs the definition table and
  **runs validation at construction** so an invalid registry cannot be built — i.e. it refuses to boot.
  `coding.py` is the one shipped definition, isolated so a second type is a new file + one registration
  line (`PRINCIPLES.md` §"Core/module contract" test).

### Import direction (no cycle — proven) — F3

- **`ticket_types` imports ONLY two leaf modules:** `tickets/contracts.py` (enums the `coding` literal
  reproduces) and `core/contracts.py` (`PlannerError`/`ErrorCode`). Both are leaves:
  - `tickets/contracts.py:1-10` imports only `planner.core.contracts` (`Priority`).
  - `core/contracts.py:1-5` imports nothing from another planner module.
- **`ticket_types` does NOT import `minds/config.py`.** The prior draft sourced `PLANNER_SKILL_NAMES`
  from `minds/config.py:18` — but that module is **not a leaf**: `minds/config.py:14` imports gateway
  runtime (`GatewayChild, SpawnFn, spawn_popen`), and importing `planner.minds.config` runs
  `planner.minds.__init__` which imports `SharedGateway` (`minds/__init__.py:20`). Since Phase 5
  (`PLAN.md:236`) makes the gateway *consume* worker profiles from `ticket_types`, importing `minds`
  here would plant a latent back-edge. **Fix (F3): the validator's reference catalogs — the set of
  known specialist-skill ids and known toolset-profile ids — are INJECTED into `build_registry(...)`
  as parameters, not imported.** The composition root (server/startup wiring, a later phase) already
  owns that wiring and is where "refuse to boot on violation" fires; it will pass the catalogs at the
  real startup call. In this ticket, tests pass the catalogs explicitly (§7).
- **Consumers import `ticket_types`:** none in this ticket (that is the point — §7 additive check).

Cycle proof: outbound edges from every module in `ticket_types` go only to `tickets/contracts.py` and
`core/contracts.py`; neither imports `ticket_types` (nor anything that does). No back-edge exists, so no
cycle is possible. A test (F3, §7) asserts an **outbound-import allowlist over the entire `ticket_types`
package**: the only cross-package `planner.*` imports any file under `ticket_types/` may make are
`planner.tickets.contracts` and `planner.core.contracts`.

---

## 2. Registry data types (`ticket_types/contracts.py`)

All frozen dataclasses; internal tables (dict/tuple) are constructor inputs kept private — callers get
derived views only (§3). Types are strings at the boundary so the registry is genuinely N-ary; the
`coding` literal sources them from the enums so parity is checkable.

```python
@dataclass(frozen=True)
class Stage:
    id: str                       # a TicketState value, e.g. "needs_success"
    label: str                    # display label, e.g. "Success"
    gating_field: str | None      # a FieldName value; None iff terminal (done / dropped)
    is_terminal: bool             # True for done and dropped

@dataclass(frozen=True)
class FieldDef:
    id: str                       # a FieldName value, e.g. "kickoff"
    label: str                    # display label, e.g. "Kickoff"

@dataclass(frozen=True)
class WorkerProfile:              # declared, INERT this ticket — validated for reference integrity only
    specialist_skill: str         # e.g. "panels-worker" (must be in the injected known-skills catalog)
    model: str | None             # None = home default (planner passes no per-session model today)
    reasoning_effort: str | None  # None = home default
    toolset_profile: str          # a toolset-profile id (must be in the injected known-toolsets catalog)

@dataclass(frozen=True)
class TransitionHook:             # declared, INERT this ticket — plan_handoff_status is NOT changed
    old_state: str                # "needs_plan"      (must be a known stage id)
    new_state: str                # "needs_implementation" (must be a known stage id)
    implementer: str              # "khushal"         (must be a known Implementer value)
    effect: str                   # "user_takeover"   (must be a known TicketStatus value)

@dataclass(frozen=True)
class WorkflowDefinition:
    type_id: str                  # "coding"
    label: str                    # "Coding"
    stages: tuple[Stage, ...]     # FULL linear order incl. leading needs_kickoff + trailing done
    dropped_stage: Stage          # the reserved exceptional terminal, outside the linear order
    fields: tuple[FieldDef, ...]  # ordered field set, led by kickoff
    worker_profile: WorkerProfile
    transition_hooks: tuple[TransitionHook, ...]
    supports_prefix_reconciliation: bool   # F5 — coding = True; consumed by t_tt03 external-work, NOT serialized
```

Notes on shape choices (everything earns its existence):
- **`stages` is the single source of order.** The gate map, advance map, ceiling range, and default
  ceiling are all *derived* from `stages` (§3) — never a second hand-written table (invariant 9).
- **`dropped_stage` is separate from `stages`** because `dropped` is outside the linear order
  (`contracts.py:26`). Keeping it out means `stages` *is* `STATE_ORDER` and the ceiling range is a clean
  slice — no filtering.
- **`supports_prefix_reconciliation` (F5)** is a backend contract field the external-work path (t_tt03,
  `PLAN.md:211`) reads to decide whether a type supports Chief prefix reconciliation. The fixture `probe`
  (t_tt02x, `PLAN.md:184`) declares it; `coding` = `True`. It is **not** serialized into the manifest —
  the manifest's consumers (CLI create/propose/scope, web labels/pickers) don't need it, and an unused
  manifest field violates "everything earns its existence." It is validated as a plain bool (no reference
  integrity needed — it names nothing external).
- **`WorkerProfile.model`/`reasoning_effort` are `str | None`.** Planner passes neither on `session.create`
  today (PLAN Phase 5); "mirror today's worker" ⇒ `None` (home default). Confirmed by BRIEF (§9).

### Manifest TypedDicts (also in `contracts.py`)

```python
class ManifestStage(TypedDict):
    id: str
    label: str
    gating_field: str | None
    is_terminal: bool

class ManifestField(TypedDict):
    id: str
    label: str

class ManifestDict(TypedDict):
    type_id: str
    label: str
    stages: list[ManifestStage]        # full order incl. needs_kickoff … done (NOT dropped)
    dropped: ManifestStage             # the exceptional terminal
    advance: dict[str, str]            # non-terminal state -> next state
    fields: list[ManifestField]        # ordered, kickoff first
    ceiling_range: list[str]           # stage order minus leading needs_kickoff
    default_ceiling: str               # first entry of ceiling_range
    worker_profile_id: str             # the specialist_skill id (the profile's stable id in v1)
```

`supports_prefix_reconciliation` is deliberately **absent** from `ManifestDict` (F5).

---

## 3. Derived-view API (`ticket_types/logic/views.py`, exposed via `Registry`) — F2

Pure functions over a `WorkflowDefinition`; the `Registry` forwards to them so callers never touch internal
tables. This API must be a **complete drop-in for everything `machine.py` does today**, because t_tt01
threads the workflow through `machine.py` in place of the module constants. Each function maps to the live
machine operation it will replace.

```python
# logic/views.py — pure, each takes a definition explicitly.

# order / indexing  (replaces machine.state_index, machine.py:42)
def state_index(defn, state_id: str) -> int          # raises validation if state_id not in the linear order
def stage_ids(defn) -> tuple[str, ...]               # every linear stage id, full order (== STATE_ORDER values)
def require_stage(defn, state_id: str) -> Stage      # the Stage for a linear state id; raises validation if unknown

# terminal recognition  (replaces machine.is_terminal, machine.py:51 — incl. exceptional dropped)
def is_terminal(defn, state_id: str) -> bool         # True for the linear terminal (done) AND dropped; raises validation on an unknown id

# forward gate  (replaces machine.gating_field, machine.py:55)
def gating_field(defn, state_id: str) -> str | None  # gate of a KNOWN state; None only for a known terminal. UNKNOWN state RAISES (see below).
def gate_map(defn) -> dict[str, str]                 # non-terminal state -> gating field (== GATING_FIELD values)

# inverse gate  (replaces the FIELD_GATES table + field_is_passed's use of it, machine.py:32/59/64)
def gated_state(defn, field_id: str) -> str          # the state a field gates; raises validation if field_id gates nothing
def has_field(defn, field_id: str) -> bool           # field_id is a declared FieldDef

# advance  (replaces machine.advance_target, machine.py:67)
def advance_target(defn, state_id: str) -> str | None  # next state of a KNOWN state; None only for a known terminal. UNKNOWN state RAISES.
def advance_map(defn) -> dict[str, str]              # non-terminal state -> next state (== ADVANCE_TARGET values)

# ceiling range  (invariant 9)
def ceiling_range(defn) -> tuple[str, ...]           # stage_ids minus the leading needs_kickoff (== WORKER_STATE_ORDER)
def default_ceiling(defn) -> str                     # ceiling_range[0]  (== "needs_success")
def linear_terminal_stage_id(defn) -> str            # the linear terminal stage id ("done") — RENAMED from terminal_status (F2)

# transition effect  (replaces machine.plan_handoff_status, machine.py:141)
def transition_effect(defn, implementer: str, old_state: str, new_state: str) -> str | None
    # returns the matching hook's effect ("user_takeover") or None; the single matching hook is keyed on
    # (old_state, new_state, implementer). No hook match -> None (parity with plan_handoff_status returning None).
```

```python
# registry.py — Registry facade
class Registry:
    def type_ids(self) -> tuple[str, ...]
    def require(self, type_id: str) -> WorkflowDefinition   # raises ErrorCode.not_found if unknown (F6 test)
    def definition_for(self, type_id: str) -> WorkflowDefinition  # alias of require, for readability at call sites
    def manifest(self, type_id: str) -> ManifestDict        # serialize_definition(require(type_id))
    # plus thin forwarders to the views above, e.g. registry.gating_field(type_id, state_id)
```

**Unknown-state vs terminal — the critical F2 distinction.** Today `machine.gating_field`/`advance_target`
return `None` for a terminal (`GATING_FIELD.get`/`ADVANCE_TARGET.get`). With per-type string states, an
*unknown* state passed to these must **not** be silently read as terminal — that would let a typo or a
foreign-type state masquerade as `done`. So:
- `gating_field(defn, state)` and `advance_target(defn, state)` return `None` **only** for a state that is
  a known terminal of `defn`; for a state id not present in `defn.stages` (and not `dropped`) they **raise**
  `PlannerError(ErrorCode.validation, "state outside the linear order", {"state": state})` — matching the
  existing `state_index` guard idiom (`machine.py:44-48`).
- `is_terminal(defn, state)` returns `True` for `done` and `dropped`, `False` for a known non-terminal,
  and **raises** for an unknown id.

**Rename (F2):** `terminal_status` → `linear_terminal_stage_id`. It returns a *stage id* string; the old
name collided with `TicketStatus` (`contracts.py:64`), an unrelated control-axis enum.

**Derivation (the load-bearing part — no hand-listed second table):**
- `stage_ids(defn)` = `tuple(s.id for s in defn.stages)`.
- `ceiling_range(defn)` = `stage_ids(defn)[1:]` — drop the leading `needs_kickoff` bookend (invariant 9;
  must equal `WORKER_STATE_ORDER`, `contracts.py:37`).
- `default_ceiling(defn)` = `ceiling_range(defn)[0]` = `"needs_success"` for `coding`.
- `gate_map(defn)` = `{s.id: s.gating_field for s in defn.stages if not s.is_terminal}`.
- `gated_state(defn, field)` = inverse of `gate_map` — the one stage whose `gating_field == field`. This
  is the registry equivalent of `FIELD_GATES` (`machine.py:32`), and validation R11 guarantees the inverse
  is one-to-one so this lookup is unambiguous.
- `advance_map(defn)` = `{stage_ids[i]: stage_ids[i+1] for i in range(len(stages)-1) if not stages[i].is_terminal}`.
- `field_ids(defn)` = `tuple(f.id for f in defn.fields)`.

---

## 4. Validator (`ticket_types/logic/validation.py`) — F1

`validate_definition(defn, *, known_skills: frozenset[str], known_toolset_profiles: frozenset[str]) -> None`
raises `PlannerError` on the first violation. `build_registry(definitions, *, known_skills,
known_toolset_profiles)` runs it over every definition **at construction** and additionally enforces
cross-definition `type_id` uniqueness — so **an invalid registry cannot be constructed** (it refuses to
boot). Uses `PlannerError(ErrorCode, message, detail)` (`core/contracts.py:143`) with the existing generic
`ErrorCode.validation` (BRIEF §9.4), distinguished by `message` + `detail`.

**Validation runs in this fixed order** so a deterministic first failure is guaranteed (F6). Non-emptiness
(R0) is checked *before* any indexing of `stages[0]`/`stages[-1]`/`ceiling_range[0]`.

| # | Rule | Check | message | detail (COMPLETE dict) |
|---|---|---|---|---|
| R0 | **non-empty** | `len(defn.stages) >= 1` and `len(defn.fields) >= 1` | `"definition has no stages"` / `"definition has no fields"` | `{"type_id": defn.type_id}` |
| R1 | **type_id uniqueness** | no two definitions share `type_id` (in `build_registry`, across the set) | `"duplicate ticket type id"` | `{"type_id": id}` |
| R2 | **unique stage ids** | all `s.id` distinct across `defn.stages` | `"duplicate stage id"` | `{"type_id", "state": dup_id}` |
| R3 | **unique field ids** | all `f.id` distinct across `defn.fields` | `"duplicate field id"` | `{"type_id", "field": dup_id}` |
| R4 | **first = needs_kickoff** | `stages[0].id == "needs_kickoff"` and `stages[0].gating_field == "kickoff"` | `"first stage must be needs_kickoff"` | `{"type_id", "first": stages[0].id}` |
| R5 | **last = done, terminal** | `stages[-1].id == "done"` and `stages[-1].is_terminal is True` | `"last stage must be done"` | `{"type_id", "last": stages[-1].id}` |
| R6 | **one linear terminal** | exactly one stage in `defn.stages` has `is_terminal is True`, and it is `stages[-1]` (no mid-order terminal, no branching) | `"linear order must have exactly one terminal"` | `{"type_id", "state": offending_id}` |
| R7 | **dropped reserved / not linear** | no linear stage has `id == "dropped"`; `"done"` appears only as the last stage; `dropped_stage.id == "dropped"` and `dropped_stage.is_terminal is True` | `"dropped may not be a linear stage"` (or `"done may not be a mid stage"`) | `{"type_id", "state": "dropped"}` (or the done id) |
| R8 | **terminals have no gate** | `stages[-1].gating_field is None` (done) and `dropped_stage.gating_field is None` | `"terminal stage may not gate a field"` | `{"type_id", "state": "done"}` (or `"dropped"`) |
| R9 | **complete gate coverage** | every non-terminal stage has a non-None `gating_field` | `"non-terminal stage must gate a field"` | `{"type_id", "state": stage.id}` |
| R10 | **gate → field reference** | every non-terminal stage's `gating_field` is the id of a declared `FieldDef` | `"gating field references an undeclared field"` | `{"type_id", "state", "gating_field"}` |
| R11 | **one-to-one gate usage** | no field is gated by two stages (the gate map inverse is injective) | `"field gated by more than one stage"` | `{"type_id", "field": field_id}` |
| R12 | **every field gated exactly once** | every declared `FieldDef.id` appears as the `gating_field` of exactly one stage (no inert extra field) | `"declared field is never gated"` | `{"type_id", "field": field_id}` |
| R13 | **fields[0] == kickoff** | `defn.fields[0].id == "kickoff"` (invariant 1 / `PLAN.md:69`) | `"first field must be kickoff"` | `{"type_id", "first_field": fields[0].id}` |
| R14 | **worker-profile skill reference** | `worker_profile.specialist_skill in known_skills` | `"worker profile references an unknown skill"` | `{"type_id", "specialist_skill": …}` |
| R15 | **worker-profile toolset reference** | `worker_profile.toolset_profile in known_toolset_profiles` | `"worker profile references an unknown toolset profile"` | `{"type_id", "toolset_profile": …}` |
| R16 | **transition-hook stage references** | each hook's `old_state`/`new_state` is a known linear stage id | `"transition hook references an unknown stage"` | `{"type_id", "state": bad_id}` |
| R17 | **transition-hook implementer reference** | each hook's `implementer` is a known `Implementer` value | `"transition hook references an unknown implementer"` | `{"type_id", "implementer": …}` |
| R18 | **transition-hook effect reference** | each hook's `effect` is a known `TicketStatus` value | `"transition hook references an unknown effect"` | `{"type_id", "effect": …}` |
| R19 | **no duplicate hook key** | no two hooks share the same `(old_state, new_state, implementer)` key | `"duplicate transition hook"` | `{"type_id", "key": [old, new, impl]}` |

Notes:
- R11 + R12 together force the gate↔field mapping to be a **bijection** over the non-terminal stages and
  the declared fields. This is what `gated_state` (§3) and the live `FIELD_GATES` inverse
  (`machine.py:32/64`, `field_is_passed`) rely on — two stages gating one field, or an inert extra field,
  both break the inverse lookup. F1's central concern.
- R17/R18 validate against the `Implementer` (`contracts.py:57`) and `TicketStatus` (`contracts.py:64`)
  enum value sets, imported from the leaf `tickets/contracts.py` — still only the allowed leaf.
- `ErrorCode.not_found` is used **only** by `Registry.require` for an unknown `type_id` (F6); every
  *validation* violation uses `ErrorCode.validation`.

---

## 5. Manifest serializer (`ticket_types/logic/manifest.py`)

`serialize_definition(defn) -> ManifestDict`. **One shape, consumed later by both CLI and web** (PLAN
invariant 4). Exact output — every key listed:

```python
{
  "type_id":         defn.type_id,                                   # "coding"
  "label":           defn.label,                                     # "Coding"
  "stages": [                                                        # full linear order, needs_kickoff … done
    {"id": s.id, "label": s.label, "gating_field": s.gating_field, "is_terminal": s.is_terminal}
    for s in defn.stages
  ],
  "dropped": {"id": "dropped", "label": <dropped_stage.label>, "gating_field": None, "is_terminal": True},
  "advance":         advance_map(defn),
  "fields": [ {"id": f.id, "label": f.label} for f in defn.fields ],
  "ceiling_range":   list(ceiling_range(defn)),
  "default_ceiling": default_ceiling(defn),
  "worker_profile_id": defn.worker_profile.specialist_skill,
}
```

`advance`/`ceiling_range`/`default_ceiling` come from the §3 derived views, so the manifest cannot drift
from the definition. JSON-round-trippable (all values str/bool/None/list/dict of those). `dropped` is
surfaced under its own key, excluded from `stages` (mirrors the code model). Worker-profile model/effort/
toolset internals are **not** serialized (inert this ticket); only the stable `worker_profile_id`.
`supports_prefix_reconciliation` is **not** serialized (F5).

---

## 6. The `coding` definition (`ticket_types/coding.py`)

Sourced from the leaf enums so the golden test asserts equality against the live constants, never a
hand-copied order. Construction:

```python
from planner.tickets.contracts import (
    STATE_ORDER, WORKER_STATE_ORDER, GATING_FIELD, ADVANCE_TARGET,
    FieldName, TicketState, TicketStatus, Implementer,
)
# stages built by walking STATE_ORDER (contracts.py:31), reading GATING_FIELD (contracts.py:73) for the gate,
# is_terminal True only for TicketState.done.
```

- **Stages** (from `STATE_ORDER`, `contracts.py:31`): `needs_kickoff, needs_success, needs_approach,
  needs_plan, needs_implementation, needs_closeout, done`. Gate of each non-terminal read from
  `GATING_FIELD` (`contracts.py:73`); `done.gating_field = None`, `done.is_terminal = True`. Labels:
  `Kickoff, Success, Approach, Plan, Implementation, Closeout, Done` (asserted exactly in the manifest test).
- **dropped_stage**: `Stage("dropped", "Dropped", None, True)` — mirrors `TicketState.dropped`
  (`contracts.py:26`).
- **fields** (from `FieldName`, `contracts.py:43`, order preserved): `kickoff, success, approach, plan,
  implementation, closeout` with labels matching the same-named stage.
- **worker_profile** (declared, INERT): `WorkerProfile("panels-worker", None, None, "default")`.
  `specialist_skill = "panels-worker"` (today's worker skill, `config.py:135`), `model = reasoning_effort =
  None`, `toolset_profile = "default"`. BRIEF §9.1/9.2 confirmed.
- **transition_hooks** (declared, INERT): one `TransitionHook("needs_plan", "needs_implementation",
  "khushal", "user_takeover")` — the exact pair from `machine.py:147-149`. `plan_handoff_status` is **not**
  modified.
- **supports_prefix_reconciliation** = `True` (F5).

`build_registry([CODING_DEFINITION], known_skills=…, known_toolset_profiles=…)` — the catalogs are passed
by the caller. In production the composition root passes them; in tests they are passed explicitly (§7).
Adding a second production type is a new file + appending to that list.

---

## 7. Tests — file, names, and asserted values

New test file: `tests/unit/test_ticket_type_registry.py`. All assertions are concrete values (no
"passes/works"); no skip/xfail/focus/empty patterns (keeps `scripts/verify.py`'s skip-scan clean). Imports
live constants from `planner.tickets.contracts` and the machine hook from `planner.tickets.logic.machine`
so drift on either side fails. A module-level helper supplies the injected reference catalogs:

```python
KNOWN_SKILLS = frozenset({"panels-worker"})            # the coding specialist skill under test
KNOWN_TOOLSET_PROFILES = frozenset({"default"})        # BRIEF §9.2
def build() -> Registry:
    return build_registry([CODING_DEFINITION], known_skills=KNOWN_SKILLS,
                          known_toolset_profiles=KNOWN_TOOLSET_PROFILES)
def validate(defn) -> None:
    validate_definition(defn, known_skills=KNOWN_SKILLS, known_toolset_profiles=KNOWN_TOOLSET_PROFILES)
```

Every negative test constructs a mutated copy of `CODING_DEFINITION` (via `dataclasses.replace` on the
definition and its stages/fields) and asserts the **complete** `PlannerError` — exact `code`, exact
`message`, and the **full `detail` dict** (F6), via a helper:

```python
def assert_raises_planner(fn, *, code, message, detail):
    with pytest.raises(PlannerError) as ei:
        fn()
    assert ei.value.code == code
    assert ei.value.message == message
    assert ei.value.detail == detail          # complete dict, not a subset
```

### Acceptance item 1 — startup validation passes + one isolated negative per invariant (BRIEF acc. 1; F1, F6)

- `test_build_registry_with_only_coding_succeeds` — `build().type_ids() == ("coding",)`;
  `build().require("coding").type_id == "coding"`.
- `test_require_unknown_type_raises_not_found` (F6) — `build().require("nope")` raises `PlannerError`,
  `code == ErrorCode.not_found`, `message == "unknown ticket type"`, `detail == {"type_id": "nope"}`.

One negative test per rule R0–R19, each asserting the full detail dict + code + message. Highlighted /
non-obvious ones:
- `test_empty_stages_rejected` (R0) — `replace(coding, stages=())` → `validation`,
  `"definition has no stages"`, `{"type_id": "coding"}`. Checked before any indexing.
- `test_empty_fields_rejected` (R0) — `replace(coding, fields=())` → `"definition has no fields"`,
  `{"type_id": "coding"}`.
- `test_duplicate_type_id_rejected` (R1) — `build_registry([coding, coding], …)` →
  `"duplicate ticket type id"`, `{"type_id": "coding"}`.
- `test_duplicate_stage_id_rejected` (R2) — two stages share `id="needs_success"` →
  `{"type_id":"coding","state":"needs_success"}`.
- `test_duplicate_field_id_rejected` (R3) — two fields share `id="success"` →
  `{"type_id":"coding","field":"success"}`.
- `test_first_stage_not_kickoff_rejected` (R4) — drop the leading kickoff so `stages[0].id ==
  "needs_success"` → `"first stage must be needs_kickoff"`, `{"type_id":"coding","first":"needs_success"}`.
- **`test_nonterminal_without_successor_rejected` (R6, ISOLATED per F6)** — final stage `Stage("done",
  "Done", None, is_terminal=False)` (right id, wrong flag): R6 fires first because there is now **no**
  terminal stage → `"linear order must have exactly one terminal"`, `{"type_id":"coding","state":"done"}`.
- **`test_wrong_terminal_id_rejected` (R5, ISOLATED per F6)** — final stage `Stage("finished", "Finished",
  None, is_terminal=True)`: R6 passes (exactly one terminal, and it is last), R5 rejects the id →
  `"last stage must be done"`, `{"type_id":"coding","last":"finished"}`.
- `test_dropped_used_as_linear_stage_rejected` (R7) — insert `Stage("dropped","Dropped","x",False)` into
  `stages` → `"dropped may not be a linear stage"`, `{"type_id":"coding","state":"dropped"}`.
- `test_terminal_stage_gating_field_rejected` (R8) — `done` given `gating_field="closeout"` →
  `"terminal stage may not gate a field"`, `{"type_id":"coding","state":"done"}`.
- `test_missing_gate_for_nonterminal_rejected` (R9) — `needs_success.gating_field=None` →
  `"non-terminal stage must gate a field"`, `{"type_id":"coding","state":"needs_success"}`.
- `test_gate_points_at_undeclared_field_rejected` (R10) — `needs_success.gating_field="ghost"` →
  `"gating field references an undeclared field"`,
  `{"type_id":"coding","state":"needs_success","gating_field":"ghost"}`.
- `test_field_gated_by_two_stages_rejected` (R11) — construct two stages gating the same field so R11 is
  the first failure (add a stage `Stage("needs_extra","Extra","success",False)` plus a matching declared
  field so R10/R12 pass, leaving `success` gated twice) → `"field gated by more than one stage"`,
  `{"type_id":"coding","field":"success"}`.
- `test_declared_field_never_gated_rejected` (R12) — append an inert `FieldDef("extra","Extra")` gated by
  nothing → `"declared field is never gated"`, `{"type_id":"coding","field":"extra"}`.
- `test_first_field_not_kickoff_rejected` (R13) — reorder so `fields[0].id="success"` →
  `"first field must be kickoff"`, `{"type_id":"coding","first_field":"success"}`.
- `test_worker_profile_unknown_skill_rejected` (R14) — `specialist_skill="not-a-skill"` →
  `"worker profile references an unknown skill"`, `{"type_id":"coding","specialist_skill":"not-a-skill"}`.
- `test_worker_profile_unknown_toolset_rejected` (R15) — `toolset_profile="nope"` →
  `"worker profile references an unknown toolset profile"`, `{"type_id":"coding","toolset_profile":"nope"}`.
- `test_hook_unknown_stage_rejected` (R16) — hook `old_state="ghost"` →
  `"transition hook references an unknown stage"`, `{"type_id":"coding","state":"ghost"}`.
- `test_hook_unknown_implementer_rejected` (R17) — hook `implementer="bob"` →
  `"transition hook references an unknown implementer"`, `{"type_id":"coding","implementer":"bob"}`.
- `test_hook_unknown_effect_rejected` (R18) — hook `effect="explode"` →
  `"transition hook references an unknown effect"`, `{"type_id":"coding","effect":"explode"}`.
- `test_duplicate_hook_key_rejected` (R19) — two hooks with the same `(needs_plan, needs_implementation,
  khushal)` key → `"duplicate transition hook"`,
  `{"type_id":"coding","key":["needs_plan","needs_implementation","khushal"]}`.

### Acceptance item 2 — parity golden test vs live constants (BRIEF acc. 2; F4)

- `test_coding_stage_order_equals_state_order` — `views.stage_ids(coding) == tuple(s.value for s in STATE_ORDER)`.
- `test_coding_gate_map_equals_gating_field` — `views.gate_map(coding) == {s.value: f.value for s, f in GATING_FIELD.items()}`.
- `test_coding_advance_map_equals_advance_target` — `views.advance_map(coding) == {s.value: t.value for s, t in ADVANCE_TARGET.items()}`.
- `test_coding_field_order_equals_fieldname` — `views.field_ids(coding) == tuple(f.value for f in FieldName)`.
- **`test_coding_field_order_equals_ticketfields_slots` (F4)** — `import dataclasses`;
  `tuple(f.name for f in dataclasses.fields(TicketFields)) == views.field_ids(coding)` — proves parity with
  the actual `TicketFields` slots (`contracts.py:108`), not just the `FieldName` enum.
- **`test_coding_field_gates_inverse_equals_field_gates` (F4)** — `from planner.tickets.logic.machine
  import FIELD_GATES`; assert `{f.value: s.value for f, s in FIELD_GATES.items()} == {field_id:
  views.gated_state(coding, field_id) for field_id in views.field_ids(coding)}` — the registry's inverse
  gate map equals the live `FIELD_GATES` (`machine.py:32`) exactly. (kickoff…closeout each gate a stage,
  so the comprehension covers every field.)
- `test_coding_ceiling_range_equals_worker_state_order` — `views.ceiling_range(coding) == tuple(s.value for s in WORKER_STATE_ORDER)`.
- `test_coding_default_ceiling_is_needs_success` — `views.default_ceiling(coding) == "needs_success"` AND
  `== WORKER_STATE_ORDER[0].value` (derived, so a drift in `WORKER_STATE_ORDER` still fails).
- `test_coding_linear_terminal_stage_id_is_done` — `views.linear_terminal_stage_id(coding) == TicketState.done.value`.

### Acceptance item 2b — derived API behavior incl. unknown-state safety (F2)

- `test_state_index_matches_state_order` — for every `s in STATE_ORDER`,
  `views.state_index(coding, s.value) == STATE_ORDER.index(s)`.
- `test_state_index_unknown_raises` — `views.state_index(coding, "ghost")` raises `PlannerError`,
  `code == ErrorCode.validation`, `detail == {"state": "ghost"}`.
- `test_is_terminal_done_and_dropped_true` — `views.is_terminal(coding, "done") is True` and
  `views.is_terminal(coding, "dropped") is True`; `views.is_terminal(coding, "needs_plan") is False`.
- `test_is_terminal_unknown_raises` — `views.is_terminal(coding, "ghost")` raises `validation`,
  `detail == {"state": "ghost"}`.
- `test_gating_field_terminal_is_none_unknown_raises` — `views.gating_field(coding, "done") is None`;
  `views.gating_field(coding, "ghost")` raises `validation`, `detail == {"state": "ghost"}` (the F2
  unknown-vs-terminal distinction).
- `test_advance_target_terminal_is_none_unknown_raises` — `views.advance_target(coding, "done") is None`;
  `views.advance_target(coding, "ghost")` raises `validation`, `detail == {"state": "ghost"}`.
- `test_gated_state_inverse` — `views.gated_state(coding, "success") == "needs_success"`;
  `views.gated_state(coding, "kickoff") == "needs_kickoff"`.
- `test_gated_state_ungated_field_raises` — `views.gated_state(coding, "ghost")` raises `validation`.
- `test_has_field` — `views.has_field(coding, "plan") is True`; `views.has_field(coding, "ghost") is False`.
- `test_require_stage_returns_stage` — `views.require_stage(coding, "needs_plan").label == "Plan"`;
  `views.require_stage(coding, "ghost")` raises `validation`.
- `test_transition_effect_matches_plan_handoff` (hook parity, also F… ) —
  `views.transition_effect(coding, "khushal", "needs_plan", "needs_implementation") == "user_takeover"`;
  `views.transition_effect(coding, "panels_worker", "needs_plan", "needs_implementation") is None`;
  `views.transition_effect(coding, "khushal", "needs_success", "needs_approach") is None`. AND assert it
  equals the live machine: `machine.plan_handoff_status(Implementer.khushal, TicketState.needs_plan,
  TicketState.needs_implementation) == TicketStatus.user_takeover` and `.value == "user_takeover"`. Proves
  the inert declaration mirrors live behavior **without** modifying `plan_handoff_status`.

### Acceptance item 3 — manifest exact shape (BRIEF acc. 3)

- `test_coding_manifest_exact` — `build().manifest("coding") == EXPECTED`:
```python
EXPECTED = {
  "type_id": "coding",
  "label": "Coding",
  "stages": [
    {"id": "needs_kickoff",        "label": "Kickoff",        "gating_field": "kickoff",        "is_terminal": False},
    {"id": "needs_success",        "label": "Success",        "gating_field": "success",        "is_terminal": False},
    {"id": "needs_approach",       "label": "Approach",       "gating_field": "approach",       "is_terminal": False},
    {"id": "needs_plan",           "label": "Plan",           "gating_field": "plan",           "is_terminal": False},
    {"id": "needs_implementation", "label": "Implementation", "gating_field": "implementation", "is_terminal": False},
    {"id": "needs_closeout",       "label": "Closeout",       "gating_field": "closeout",       "is_terminal": False},
    {"id": "done",                 "label": "Done",           "gating_field": None,             "is_terminal": True},
  ],
  "dropped": {"id": "dropped", "label": "Dropped", "gating_field": None, "is_terminal": True},
  "advance": {
    "needs_kickoff": "needs_success", "needs_success": "needs_approach",
    "needs_approach": "needs_plan", "needs_plan": "needs_implementation",
    "needs_implementation": "needs_closeout", "needs_closeout": "done",
  },
  "fields": [
    {"id": "kickoff", "label": "Kickoff"}, {"id": "success", "label": "Success"},
    {"id": "approach", "label": "Approach"}, {"id": "plan", "label": "Plan"},
    {"id": "implementation", "label": "Implementation"}, {"id": "closeout", "label": "Closeout"},
  ],
  "ceiling_range": ["needs_success", "needs_approach", "needs_plan", "needs_implementation", "needs_closeout", "done"],
  "default_ceiling": "needs_success",
  "worker_profile_id": "panels-worker",
}
```
- `test_coding_manifest_json_roundtrips` — `json.loads(json.dumps(build().manifest("coding"))) == EXPECTED`.
- `test_manifest_omits_prefix_reconciliation` (F5) — `"supports_prefix_reconciliation" not in
  build().manifest("coding")` (the capability stays a backend contract field, not a manifest key).
- `test_coding_supports_prefix_reconciliation` (F5) —
  `build().require("coding").supports_prefix_reconciliation is True`.

### Acceptance item 4 — additive-only / no production consumer (BRIEF acc. 4; F3 outbound allowlist, F6 matcher)

- **`test_ticket_types_outbound_imports_allowlisted` (F3)** — walk every `*.py` under
  `src/planner/ticket_types/`, parse with `ast`, collect every imported module. Assert the only
  cross-package `planner.*` targets are in `{"planner.tickets.contracts", "planner.core.contracts"}`
  (intra-package `planner.ticket_types.*` imports allowed). The offending set must `== set()`.
- **`test_no_production_module_imports_ticket_types` (F6 hardened matcher)** — walk every `*.py` under
  `src/planner/` **excluding** `src/planner/ticket_types/`, parse with `ast`, and flag a file if any of
  these resolve to `planner.ticket_types[.*]`:
  - `ast.Import` alias name `== "planner.ticket_types"` or starting `"planner.ticket_types."`
    (catches `import planner.ticket_types`, `import planner.ticket_types.registry`);
  - `ast.ImportFrom` with `module == "planner.ticket_types"` or starting `"planner.ticket_types."`
    (catches `from planner.ticket_types import …`, `from planner.ticket_types.x import …`);
  - `ast.ImportFrom` with `module == "planner"` and an alias `name == "ticket_types"`
    (catches `from planner import ticket_types`);
  - any `ImportFrom` with `level > 0` resolved against the file's package to `planner.ticket_types[.*]`
    (catches relative imports).
  Assert the offending file set `== []`.

**`./verify` unchanged:** the only new files are under `src/planner/ticket_types/` plus the one test file;
no edit to `tickets/contracts.py`, `machine.py`, `resolution.py`, `fields_codec.py`, `db.py`, or any
consumer. Existing suites run identically; the orchestrator runs `./verify` at integration.

---

## 8. What this ticket does NOT touch (guard rails)

Per BRIEF scope-out and hard constraints: no edit to `tickets/contracts.py` enums/tables, `machine.py`
(`plan_handoff_status` unchanged; `FIELD_GATES`/`STATE_ORDER`/`WORKER_STATE_ORDER`/`GATING_FIELD`/
`ADVANCE_TARGET` are read-only inputs to the golden tests, never relaxed), `resolution.py`,
`fields_codec.py`, `db.py`, CLI, API, views, web, or any consumer. No new `EventKind`. No new `ErrorCode`
member. No production wiring imports the new module.

---

## 9. Resolved decisions (orchestrator-confirmed in BRIEF §"Decisions on the planner's open questions")

All four are inert this ticket and only fix the asserted manifest/validator values:
1. **Coding worker profile** — `specialist_skill="panels-worker"`, `model=None`, `reasoning_effort=None`,
   `toolset_profile="default"` (mirrors today; session.create passes no model/effort; toolsets are
   home-default). t_tt05 makes model/toolset real.
2. **Reference catalogs** — validated against injected catalogs; tests use `known_skills={"panels-worker"}`,
   `known_toolset_profiles={"default"}`. Real named profiles are a t_tt05 construct. (Injection, not
   import — F3.)
3. **Stage/field labels** — `Kickoff / Success / Approach / Plan / Implementation / Closeout / Done`,
   asserted exactly; the manifest becomes the single label source. If `web/src/lib/ui.ts` already carries
   canonical label strings, the implementer reuses those exact strings to shrink the eventual t_tt04b diff;
   otherwise these stand.
4. **ErrorCode** — reuse the existing generic `ErrorCode.validation` for every registry violation (specific
   `message` + `detail`); `ErrorCode.not_found` only for `Registry.require` on an unknown type. No new core
   enum member (additive-zero).
