# t_tt01 — Implementation plan: parameterize the state-machine engine by a WorkflowDefinition

Phase 1 of `orchestration/ticket-types-redesign/PLAN.md`. Route `coding` through the registry
t_tt00 built, **parity-preserving for every valid canonical coding input** — every state/field/scope
the coding lifecycle can actually produce behaves byte-identically. The three *intentional*
behavioral tightenings (all unreachable under valid coding data, so not parity breaks) are named
explicitly: unknown-state errors now say `"state outside the linear order"` (§3.1), a top-level
JSON field key not in the definition is rejected instead of silently ignored (§7.3), and a note
write to an undeclared field is rejected instead of misrouted (§7.2). The engine becomes
definition-driven and string-id-native; the existing `./verify` suite passes with **zero edits to
existing test assertions** (the sole exception is the F6 guard test, §8, which t_tt01 is defined to
retire).

This plan is grounded in the code as it stands today. Note two facts that differ from the BRIEF's
line references and must be honored:

1. **Registry lives at `src/planner/ticket_types/` with `registry.py`, `contracts.py`, `coding.py`
   at the package root** (NOT under `logic/`). The derived views are at
   `src/planner/ticket_types/logic/views.py`. The public API is `planner.ticket_types` →
   `build_registry`, `Registry`, `CODING_DEFINITION`; the `Registry` methods are thin
   per-`type_id` forwarders to `views.*` (registry.py:78-123). The views themselves take a
   `WorkflowDefinition` (`views.state_index(defn, state_id)` etc.).
2. **Two import-graph guard tests already exist** in `tests/unit/test_ticket_type_registry.py`
   and one of them (F6, `test_no_production_module_imports_ticket_types`, line 754) **forbids any
   production module from importing `ticket_types`**. t_tt01's whole purpose is to make production
   consume the registry, so F6 as written is in direct conflict with this ticket. This is the
   single unavoidable existing-test change; it is handled explicitly in §8 and flagged as Risk 1.

---

## 0. The load-bearing constraint that shapes every decision

**Existing tests call the pure engine and the `data.*` writers with fixed arity and NO
definition/registry argument.** These calls cannot be edited (parity). Concretely:

| Existing call (must keep working unchanged) | File:line |
|---|---|
| `machine.advance_target(TicketState.needs_closeout) is TicketState.done` | test_ticket_lifecycle.py:120-124 |
| `machine.has_pending_parked_proposal(t)` | test_tickets_engine.py:176, 242 |
| `machine.has_pending_gating_proposal(t.state, t.fields)` | test_tickets_engine.py:713; test_a03… |
| `resolution.decide_edit_value(ticket, FieldName.success, "new success", "human")` | test_value_edit_logic.py:123,155,171,184,196,206,216 |
| `fields_codec.fields_from_json(legacy)` (ONE arg) | test_tickets_engine.py:133 |
| ~130 `data.*` writer calls (`file_proposal`, `accept_proposal`, `edit_field_value`, …) with kwargs, no definition | test_tickets_engine.py, test_value_edit_logic.py, test_ticket_lifecycle.py, test_ticket_edit_api.py, test_readiness_actions.py, test_chief_external_work.py |

Two hard consequences:

- **A. No pinned signature may gain a *required* parameter.** The ~130 `data.*` writer calls and the
  directly-called pure functions (`advance_target`, `has_pending_*`, `decide_edit_value`,
  `fields_from_json`) are fixed-arity and cannot be edited.
- **B. But an *optional keyword-only* `definition` parameter, defaulting to coding via the bridge,
  breaks nothing** — existing fixed-arity callers omit it and get coding (parity), while new callers
  (the string-id test §10.C, and t_tt02 threading each row's stored type) pass a definition and get
  genuinely multi-definition behavior.

**The seam (justified in §2, corrected per independent review F1):** keep the process-wide singleton
registry + `coding_bridge`, but the parameterized machine/resolution/admission/codec operations take
an **explicit optional keyword-only `definition` parameter**:

```python
def advance_target(state: str, *, definition: WorkflowDefinition | None = None) -> ...:
    definition = definition or coding_bridge.coding_definition()
    ...
```

The bridge accessor is the **compatibility default**, NOT the sole resolution point. This matters:
resolving `coding_definition()` *internally with no override* would make the engine permanently
single-definition — in t_tt02 a `probe` ticket's `machine.advance_target("needs_alpha")` would always
consult coding and raise "state outside the linear order." The optional-parameter seam is the one
t_tt02 **extends** (thread the row's definition into the same param) rather than re-refactors. The
underlying views already have this explicit shape (`views.advance_target(defn, state_id)`,
views.py:111), so the engine just forwards the (defaulted) definition to them. **The definition is
NOT placed on `Decision`** — `Decision` is transition *output* (decisions.py:19), not execution
context.

---

## 1. What "definition-driven, string-id-native" means precisely here

**"String-id-native" is scoped to the linear-order lookups only (Tier 1), NOT scope or field storage
(Tier 2)** — per review F2. Making scope/field-storage string-native in t_tt01 would fail strict mypy
and crash `_apply_decision`. The honest claim:

- **Tier 1 (string-id-native): the linear-order lookups.** `state_index`, `is_terminal`,
  `gating_field`, `advance_target`, `field_is_passed`, `at_or_beyond_ceiling`, `auto_accept_target`
  take `str` ids + an optional `definition`, delegate to `views.*`, and must never require enum
  *membership* — a future `"needs_alpha"` (not in `TicketState`) flows. §10.C exercises exactly these
  with a non-enum state id.
- **Tier 2 (coding-bound, deferred to t_tt02x/t_tt02): scope + field storage.** `resolve_scope`,
  `validate_ceiling`, `has_pending_gating_proposal`, and the `TicketFields`/`FieldSlot`/codec-slot
  operations stay `TicketState`/`FieldName`-typed. `ScopePair.next_ceiling` is
  `TicketState | Literal["none"]` (contracts.py:119) and `get_slot` requires `FieldName`
  (fields_codec.py:85); a bare `str` here would return into `ScopePair`, reach `_apply_decision`'s
  `.value`, and crash under strict mypy + at runtime. Genericizing these is explicitly out of scope
  (see §Out-of-scope). §10.C never sends a non-enum id through scope or field storage.
- **The enum-return obligation.** **Three** existing assertions identity-compare an engine result to
  an enum member: `machine.advance_target(...) is TicketState.done / .needs_success / .needs_closeout`
  (test_ticket_lifecycle.py:**120, 121, 123** — corrected from "one" per review F2). `StrEnum` members
  are singletons, so `TicketState("done") is TicketState.done` is **True** and a bare `"done"` `is
  TicketState.done` is **False** (verified). `advance_target`'s `_as_state` re-wrap satisfies all
  three for coding while returning a bare string for a foreign id (§4). No OTHER engine result is
  identity-compared to an enum in the suite (grep-verified: only those three lines).
- **`TicketFields`/`FieldSlot` stay a fixed 6-field struct.** t_tt01 does NOT rebuild the codec into a
  generic slot map. `get_slot`/`with_slot` keep `FieldName` identity dispatch (correct for coding,
  whose field ids equal the enum values). §10.C exercises the state lookups (Tier 1) with a non-enum
  stage id; it does NOT store `TicketFields` slots under non-enum field names (Tier-2, deferred).

---

## 2. The Registry seam (and the no-cycle proof)

### 2.1 The two directions the guards already enforce
- **F3** (`test_ticket_types_outbound_imports_allowlisted`, test_ticket_type_registry.py:725):
  `ticket_types/*` may import ONLY `planner.tickets.contracts` and `planner.core.contracts`
  (`_ALLOWED_OUTBOUND`, line 698). We do NOT touch `ticket_types/`, so F3 stays green by
  construction.
- **F6** (`test_no_production_module_imports_ticket_types`, line 754): today NO production module
  imports `ticket_types`. t_tt01 must break exactly this one, and only intentionally — see §8.

### 2.2 The seam: a new bridge module `planner.tickets.logic.coding_bridge`

Create one new leaf module `src/planner/tickets/logic/coding_bridge.py`:

- It imports `from planner.ticket_types import build_registry, CODING_DEFINITION` and
  `from planner.ticket_types.contracts import WorkflowDefinition`.
- It builds a **process-wide singleton registry** lazily on first use, with **injected catalogs**
  (never importing `minds/config`): the catalogs are small literal frozensets defined in this module
  — `known_skills = frozenset({"panels-worker"})` and `known_toolset_profiles = frozenset({"default"})`
  — matching `coding`'s `WorkerProfile` (`specialist_skill="panels-worker"`,
  `toolset_profile="default"`, coding.py:77-82) and the values t_tt00's own test uses
  (test_ticket_type_registry.py:42-43). These are the reference catalogs the registry validator needs
  (R14/R15). They are **inlined here, not imported from `minds/config`**, honoring D102's no-cycle
  seam rule.
- Public API:
  - `coding_registry() -> Registry` — build-once accessor (module-global, guarded so it builds a
    single validated registry per process; validation runs at build, so a broken definition refuses
    to boot exactly as D102 requires).
  - `coding_definition() -> WorkflowDefinition` — returns `coding_registry().require("coding")`.
    This is the **default provider**: a parameterized engine op with `definition=None` calls this to
    resolve coding.
  - It also re-exports `views` (`from planner.ticket_types.logic import views as _views; views = _views`)
    so engine modules reach the derived views WITHOUT importing `ticket_types` themselves (keeps the
    F6 importer set to `{coding_bridge}` — §8), and re-exports `WorkflowDefinition` for annotations.

**Seam shape (corrected per review F1):** the parameterized machine/resolution/admission/codec ops
take an optional keyword-only `definition: WorkflowDefinition | None = None` and resolve
`definition = definition or coding_bridge.coding_definition()` at the top. The bridge accessor is the
compatibility DEFAULT — existing fixed-arity callers omit the param and get coding (parity), new
callers pass a definition and drive a different workflow through the SAME code. This is the seam
t_tt02 **extends** (thread each row's stored-type definition into the same param), not re-refactors.
Putting the definition on the parameter (not in a module-global resolution, not on `Decision`) is
what makes the engine genuinely N-ary while staying parity-safe today.

### 2.3 Import-graph and no-cycle proof

New/changed production import edges:

```
tickets/logic/coding_bridge.py  ──imports──▶  planner.ticket_types            (package __init__)
                                                    │
                                 ┌──────────────────┼────────────────────────────┐
                                 ▼                  ▼                            ▼
                       ticket_types.coding   ticket_types.registry     ticket_types.logic.*
                                 │                  │                            │
                                 ▼                  ▼                            ▼
                    planner.tickets.contracts   (validation.py also imports)  core.contracts
                                                planner.tickets.contracts
```

- `ticket_types` (all of it) imports only `planner.tickets.contracts` + `planner.core.contracts`
  (F3-enforced). **`planner.tickets.contracts` is a pure leaf**: it imports only `planner.core.contracts`
  and stdlib (contracts.py:1-10). It imports nothing from `tickets.logic`, `tickets.data`, or
  `ticket_types`. So `ticket_types → tickets.contracts` is an edge to a sink; **no back-edge exists**.
- `coding_bridge` imports `ticket_types`; `ticket_types` never imports `tickets.logic` (would be a
  new module `tickets.logic.coding_bridge` — F3 forbids `ticket_types` importing `tickets.logic.*`,
  and we are not adding such an import). So the only cycle risk would be
  `tickets.logic.coding_bridge → ticket_types → tickets.logic.coding_bridge`, which does not exist:
  `ticket_types` reaches only `tickets.contracts`, never `tickets.logic`.
- `machine.py`, `resolution.py`, `admission.py`, `external_work.py`, `fields_codec.py`, `data.py`,
  `readiness.py`, `sprints/views.py`, `tickets/views.py`, `employee_step_runner.py` import
  `coding_bridge` (a sibling leaf in `tickets.logic`, or an inward import from runtime/sprints), and
  through it reach the registry. None of them imports `ticket_types` directly — keeping the F6
  intentional-break to a **single, named allowance** (§8), not a scatter.

**Cycle check, concretely:** the strongly-connected component containing `coding_bridge` is just
`{coding_bridge}`. `ticket_types.*` forms its own component whose only outbound cross-package edges
land in the two leaves. `tickets.contracts` and `core.contracts` are sinks. No SCC spans two
packages. Proven acyclic.

**Composition-root note.** There is no eager wiring required in `core/server.py` for correctness:
the bridge builds lazily and validates on first `coding_registry()` call. Optionally the server
lifespan MAY call `coding_bridge.coding_registry()` once at startup so a malformed definition fails
boot loudly rather than on first ticket op — but this is not required for parity and adds an import
of `coding_bridge` (not `ticket_types`) to server.py. **Decision: do the eager warm call in the
`create_app` lifespan** (server.py `create_app`, near app.state population, line ~172), because
"validate and refuse to boot on violation" is the registry's stated contract and a lazy-only build
would defer that to the first request. This is an added call, not a changed signature, so it is
parity-safe.

---

## 3. machine.py — the 12 functions: exact new signatures and delegation

`machine.py` today imports `STATE_ORDER, WORKER_STATE_ORDER, GATING_FIELD, ADVANCE_TARGET` and
defines `FIELD_GATES` (machine.py:10-39). After this ticket **machine.py imports none of those four
constants and defines no `FIELD_GATES`**; it imports **only `coding_bridge`** (not `ticket_types`
nor `views` directly — §3.1) and delegates to the views through `coding_bridge.views.*` /
`coding_bridge`'s thin wrappers.

Import block becomes (machine.py:10-25):
- Keep: `FieldName, TicketState, TicketStatus, TicketFields, ScopePair, NextCeiling, AtCap, NO_FURTHER,
  Implementer` (still used as *types* and for the reserved-bookend `NO_FURTHER` sentinel + the
  `TicketState`/`FieldName` return re-wrap).
- Drop: `ADVANCE_TARGET, GATING_FIELD, STATE_ORDER, WORKER_STATE_ORDER`.
- Add: `from planner.tickets.logic import coding_bridge` **only**. machine.py must NOT import
  `planner.ticket_types` / `views` directly (that would make it a second F6 importer). It reaches the
  views exclusively through the bridge's re-export.

**Delegation shape (keeps the F6 allowance to `coding_bridge` alone):** `coding_bridge` re-exports the
views under a stable attribute — `from planner.ticket_types.logic import views as _views` then
`views = _views` — AND provides the definition accessor. machine.py then does
`defn = coding_bridge.coding_definition()` and `coding_bridge.views.state_index(defn, state)`. Only
`coding_bridge` names `ticket_types` in any import; every other module reaches the views transitively
through it, so the F6 importer set is exactly `{coding_bridge}` (§8).

**Parameter policy (read before the tables).** Two tiers, per review F2 — this is the honest scope of
"string-id-native" in t_tt01:

- **Tier 1 — string-id-native, definition-parameterized (the linear-order lookups).** These take
  `state`/`field` as `str` (so a foreign `"needs_alpha"` is admissible) AND an optional keyword-only
  `definition: WorkflowDefinition | None = None`, resolving `definition = definition or
  coding_bridge.coding_definition()` and forwarding it to `coding_bridge.views.*`. Existing callers
  omit the param → coding (parity); the §10.C test + t_tt02 pass a definition → N-ary. These are the
  ONLY functions the string-id proof (§10.C) exercises with a non-enum state id.
- **Tier 2 — coding-bound (scope + field-storage).** `resolve_scope`, `validate_ceiling`,
  `has_pending_gating_proposal` stay **`TicketState`/`FieldName`-typed and coding-resolved**. They
  cannot be made string-native in t_tt01 without failing strict mypy (`ScopePair.next_ceiling` is
  `TicketState | Literal["none"]`, contracts.py:119; `get_slot` requires `FieldName`,
  fields_codec.py:85) — a bare `str` flowing into `ScopePair` then `_apply_decision`'s `.value` would
  crash. **Genericizing `ScopePair`/`TicketFields` is deferred to when `probe` actually stores fields
  (t_tt02x/t_tt02)** — stated in §Out-of-scope. These functions still stop importing the module
  constants (they read the definition via the bridge default), but their *signatures* remain enum-typed.

Caller signatures are NOT retyped: `admission.check_agent_proposal(state: TicketState, …)`,
`data.*(field: FieldName, …)` keep their `StrEnum` annotations — a `StrEnum` *is* a `str`, satisfying
a `str` param with no change; the engine calls `str(x)` internally (no-op for a `StrEnum`). Only the
Tier-1 `machine.py` params change to `str`. The overloaded RETURNS of `advance_target`,
`auto_accept_target`, `gating_field` (§4) preserve the enum type for coding callers so `.value`/
identity uses type-check unchanged.

### Tier 1 — string-id-native, `*, definition=None` (forward to views)

Each resolves `defn = definition or coding_bridge.coding_definition()` at the top.

| # | Function | New signature | Delegation |
|---|---|---|---|
| 1 | `state_index` | `state_index(state: str, *, definition=None) -> int` | `return coding_bridge.views.state_index(defn, str(state))` — same `validation`/"state outside the linear order" (views.py:22-25). |
| 2 | `is_terminal` | `is_terminal(state: str, *, definition=None) -> bool` | `return coding_bridge.views.is_terminal(defn, str(state))`. **Unknown-id drift (§3.1, out of coding-parity envelope):** today `is_terminal("ghost")` returns `False`; views RAISES. Unreachable under coding (all ids known; grep-verified no test passes an unknown id). |
| 3 | `gating_field` | overloaded (§4): `(TicketState, *, definition=None) -> FieldName \| None` / `(str, *, definition=None) -> FieldName \| str \| None` | `gf = coding_bridge.views.gating_field(defn, str(state)); return _as_field(gf) if gf is not None else None`. `_as_field` re-wraps to `FieldName` for a coding field id, bare str for foreign (§4). **Unknown-STATE drift (§3.1):** today `gating_field("ghost")` returns `None`; views RAISES. Unreachable under coding. |
| 4 | `field_is_passed` | `field_is_passed(field: str, state: str, *, definition=None) -> bool` | Replace `FIELD_GATES[field]` with `coding_bridge.views.gated_state(defn, str(field))`; `return views.state_index(defn, str(state)) > views.state_index(defn, gated)`. |
| 5 | `advance_target` | overloaded (§4): `(TicketState, *, definition=None) -> TicketState` / `(str, *, definition=None) -> TicketState \| str` | `tgt = coding_bridge.views.advance_target(defn, str(state))`; if `None` raise the same `PlannerError(validation, "state has no advance target", {"state": str(state)})`; else `return _as_state(tgt)` (§4). Preserves the **three** `advance_target(...) is TicketState…` identity assertions (test_ticket_lifecycle.py:120, 121, 123). **Unknown-id drift (§3.1):** unreachable under coding. |
| 6 | `auto_accept_target` | overloaded (§4): `(TicketState, str, str, *, definition=None) -> TicketState \| None` / `(str, str, str, *, definition=None) -> TicketState \| str \| None` | Body unchanged: `if is_terminal(state, definition=defn): return None`; `if field != gating_field(state, definition=defn): return None` (was `is not`, §5); `target = advance_target(state, definition=defn)`; `if state_index(target, definition=defn) > state_index(ceiling, definition=defn): return None`; `return target`. Threads `defn` to its sub-calls so a single passed definition governs the whole op. For a `TicketState` arg the overload narrows to `TicketState \| None`. |
| 7 | `at_or_beyond_ceiling` | `at_or_beyond_ceiling(state: str, ceiling: str, *, definition=None) -> bool` | `return state_index(state, definition=defn) >= state_index(ceiling, definition=defn)`. |

### Tier 2 — coding-bound (enum-typed, definition resolved via the bridge default; NOT string-native)

| # | Function | Signature (unchanged enum typing) | Delegation |
|---|---|---|---|
| 8 | `validate_ceiling` | `validate_ceiling(ceiling: TicketState, *, definition=None) -> None` | Replace `ceiling not in WORKER_STATE_ORDER` with `str(ceiling) not in coding_bridge.views.ceiling_range(defn)`; same `PlannerError(scope_invalid, "ceiling must be a linear state", {"ceiling": str(ceiling)})`. Stays `TicketState`-typed (scope is coding-bound, Tier 2). |
| 9 | `resolve_scope` | `resolve_scope(new_state: TicketState, next_ceiling: NextCeiling \| None, at_cap: AtCap \| None, *, definition=None) -> ScopePair` | Stays `TicketState`-typed — do NOT make `new_state`/`next_ceiling` `str` (would return a bare str into `ScopePair.next_ceiling: TicketState \| Literal["none"]` and crash `_apply_decision`'s `.value`, review F2). `None`/missing checks unchanged. `if next_ceiling == NO_FURTHER:` returns `ScopePair(next_ceiling=new_state, …)` — `new_state` is a `TicketState`, type-clean. **KEEP `if not isinstance(next_ceiling, TicketState):`** (its `scope_invalid`/"unknown next_ceiling" branch is the coding error surface). Replace only `next_ceiling not in WORKER_STATE_ORDER` with `str(next_ceiling) not in coding_bridge.views.ceiling_range(defn)`; keep `state_index(next_ceiling, definition=defn) < state_index(new_state, definition=defn)`; same `scope_invalid` payload. Returns `ScopePair` with a `TicketState` `next_ceiling`, so `_accept_gating_proposal`'s `assert isinstance(ceiling, TicketState)` (resolution.py:78) holds (§7.1). A definition-native string-ceiling API is t_tt02+. |
| 10 | `has_pending_gating_proposal` | `has_pending_gating_proposal(state: TicketState, fields: TicketFields, *, definition=None) -> bool` | Stays `TicketState`-typed (field-storage is coding-bound, Tier 2). `field = gating_field(state, definition=defn)`. For coding, `gating_field(a_TicketState, …)` narrows to `FieldName | None` (§4 overload) → `get_slot(fields, field)` type-checks (needs `FieldName`, fields_codec.py:85). `if field is None: return False; return fields_codec.get_slot(fields, field).proposal is not None`. Existing call `has_pending_gating_proposal(t.state, t.fields)` unchanged. **Note:** because it is enum-typed, a non-enum synthetic field is NOT sent through the fixed six-field codec — §10.C exercises the codec/slot map only with coding, never a foreign field id (review F2). |
| 11 | `has_pending_parked_proposal` | `has_pending_parked_proposal(ticket: Ticket, *, definition=None) -> bool` | `return has_pending_gating_proposal(ticket.state, ticket.fields, definition=definition)`. Existing call `has_pending_parked_proposal(t)` unchanged. |
| 12 | `plan_handoff_status` | `plan_handoff_status(implementer, old_state, new_state) -> TicketStatus \| None` | **Unchanged in v1** (no definition param — the hook is inert this ticket). Keeps its literal `old_state == needs_plan and new_state == needs_implementation and implementer == khushal`. Convert its two `is` comparisons to `==` (§5), naming the reserved states literally. Do NOT route through `views.transition_effect` (later wiring). |

**Note on `defn` retrieval:** each Tier-1/Tier-2 function resolves `definition or
coding_bridge.coding_definition()` at entry — a dict lookup on a built singleton, negligible, no I/O.
Resolving call-locally (not a module global captured at import) avoids import-order fragility, and
the `or`-default means an explicit `definition=` overrides it (the seam t_tt02 extends).

### 3.1 The unknown-state divergence — scoped and accepted (not a coding-parity break)

`views.*` raise `"state outside the linear order"` for an **unknown** (non-linear, non-dropped) id,
whereas today's machine returns `False`/`None`/a-different-raise for the same unknown id. This is a
real behavioral difference **only for ids that are neither a coding linear state nor `dropped`**.
Under coding, every id the engine sees is a real ticket state (loaded from a row that passed the DB
`CHECK`, or a reserved bookend), so the divergent branch is **unreachable** — verified: no existing
test passes an unknown id to `is_terminal`/`gating_field`/`advance_target` (all callers pass
`ticket.state` or a `TicketState`). Coding parity therefore holds byte-for-byte. The `probe`/synthetic
string-id test (§10.C) exercises only *known* ids of its *own* definition, so it also never hits the
divergence. **Do not add unknown-id shim logic to restore the old `False`/`None`** — it is dead code
under coding and would only re-couple the engine to a closed-world assumption. This is an accepted,
named narrowing of "byte-identical" to "byte-identical over every input coding can actually produce."

**`fields_codec` must reach `views` through the bridge, not import `ticket_types` directly.** F6
walks the AST for ANY import of `ticket_types` (including `TYPE_CHECKING`-guarded ones). To keep the
F6 allowance to the single `coding_bridge` module, `coding_bridge` exposes thin wrappers
(`coding_bridge.field_ids(defn)`, `coding_bridge.has_field(defn, fid)` — forwarding to `views`), and
`fields_codec`/`machine`/etc. call **those**, importing only `coding_bridge`. No engine module other
than `coding_bridge` names `ticket_types` in any import, guarded or not.

---

## 4. The `_as_state` / enum-return helper (the enum-vs-string resolution)

The runtime shape is: an enum member (StrEnum singleton) for a coding id, a bare `str` for a foreign
id. The honest runtime return is `TicketState | str` (resp. `FieldName | str`). But the two
resolution call sites feed the result into `Decision.new_state` (typed `TicketState | None`), so mypy
must see `TicketState` there. **The overloads must live on the PUBLIC functions themselves**
(`advance_target`, `gating_field`), not only on private helpers — a helper overload does not narrow
the public function's own return (Codex-flagged). Shape:

```python
from typing import overload

# private, un-overloaded — always returns the honest union:
def _as_state(state_id: str) -> TicketState | str:
    try: return TicketState(state_id)          # StrEnum singleton for a coding id
    except ValueError: return state_id          # foreign id: bare string, engine stays N-ary
def _as_field(field_id: str) -> FieldName | str:
    try: return FieldName(field_id)
    except ValueError: return field_id

# PUBLIC advance_target: overloads narrow a TicketState arg → TicketState; optional definition seam.
@overload
def advance_target(state: TicketState, *, definition: WorkflowDefinition | None = None) -> TicketState: ...
@overload
def advance_target(state: str, *, definition: WorkflowDefinition | None = None) -> TicketState | str: ...
def advance_target(state: str, *, definition: WorkflowDefinition | None = None) -> TicketState | str:
    defn = definition or coding_bridge.coding_definition()
    tgt = coding_bridge.views.advance_target(defn, str(state))
    if tgt is None:
        raise PlannerError(ErrorCode.validation, "state has no advance target", {"state": str(state)})
    return _as_state(tgt)

# PUBLIC gating_field likewise:
@overload
def gating_field(state: TicketState, *, definition: WorkflowDefinition | None = None) -> FieldName | None: ...
@overload
def gating_field(state: str, *, definition: WorkflowDefinition | None = None) -> FieldName | str | None: ...
def gating_field(state: str, *, definition: WorkflowDefinition | None = None) -> FieldName | str | None:
    defn = definition or coding_bridge.coding_definition()
    gf = coding_bridge.views.gating_field(defn, str(state))
    return _as_field(gf) if gf is not None else None

# (auto_accept_target carries the same overload pair + `*, definition=None`, threading defn to its
#  advance_target / gating_field / state_index sub-calls — §3 Tier 1 #6.)
# WorkflowDefinition is referenced via coding_bridge.WorkflowDefinition (re-exported) — machine.py
# must NOT import ticket_types directly (F6, §3.1).
```

- **Why the overload on the public function works and the helper-only overload does not.** At
  resolution.py:53 (`new_state = machine.advance_target(ticket.state)`) and :166, `ticket.state` is a
  `TicketState`, so the first overload (`(TicketState) -> TicketState`) applies → mypy infers
  `TicketState` → `Decision.new_state`/`_state_change(..., TicketState, ...)` type-check with no cast
  and **`Decision.new_state` stays `TicketState | None`** (not widened — the t_tt02 concern is
  avoided). At the string-id test (§10.C), the call passes a plain `str`, the second overload applies
  → `TicketState | str`, and the test asserts the bare-string branch. Same reasoning for
  `gating_field`: coding callers (`employee_step_runner.py:42` `gating.value`, `admission.py:41,55`,
  resolution.py:165, sprints/views, tickets/views) pass `ticket.state`/`TicketState(state)` → narrow
  to `FieldName | None` → `.value` and identity on the result type-check unchanged.
- **`auto_accept_target` (Tier 1 #6)** returns `advance_target(...)`'s result. At its one production
  call site (resolution.py:107) the argument is `ticket.state` (a `TicketState`). It carries the same
  overload pair (`(TicketState, str, str, *, definition=None) -> TicketState | None` /
  `(str, str, str, *, definition=None) -> TicketState | str | None`) so the coding path stays
  `TicketState | None` and the resolve_scope assert (resolution.py:78) is satisfied — removing the
  "statically false return" objection. It threads its received `definition` into every sub-call
  (`is_terminal`/`gating_field`/`advance_target`/`state_index`) so one passed definition governs the
  whole op (the N-ary property t_tt02 relies on).
- **Fallback if overload resolution misbehaves under the repo's mypy config:** a `cast(TicketState,
  ...)` at the two resolution assignment sites (resolution.py:53, :166) — parity-safe because coding
  always yields an enum member — NOT widening `Decision`. Confirm mypy clean in the diff review;
  prefer the overloads, use the cast only if strict-mode overload matching fails.
- **This is the entire enum-vs-string resolution.** Everywhere else the Tier-1 engine takes/compares
  string ids and returns `bool`/`int`/`None`. Only `advance_target`/`auto_accept_target` re-wrap to
  `TicketState` and `gating_field` re-wraps to `FieldName`; all are overloaded on the public boundary
  and `try/except`-guarded to stay N-ary-safe.
- **Strict-mypy proof obligation (review F2).** The repo runs `strict = true` (pyproject.toml:40).
  The plan's typing is not "trust me" — the diff review must include mypy-checked cases proving every
  overload resolves as claimed, ESPECIALLY the two spots the first two self-reviews got wrong:
  (i) `resolve_scope("needs_x", NO_FURTHER, AtCap.propose)` must NOT be callable with a bare `str`
  `new_state` — `resolve_scope` stays `TicketState`-typed (Tier 2), so a `str` arg is a mypy error by
  design, proving no bare string can reach `ScopePair.next_ceiling`; (ii)
  `has_pending_gating_proposal(a_TicketState, fields)` must type-check because `gating_field` narrows
  to `FieldName | None` for a `TicketState` arg → feeds `get_slot(fields, FieldName)`. Add a
  `reveal_type` assertion in a mypy-run test (or a `typing`-only module compiled under the CI mypy
  gate) for `advance_target(a_TicketState)` → `TicketState` and `advance_target(a_str)` →
  `TicketState | str`. If overload resolution misbehaves under the repo config, the fallback is a
  `cast(TicketState, ...)` at resolution.py:53/:166 only — never widening `Decision` or `ScopePair`.

**Why not keep `TicketState` params and `.value`-normalize?** Because `resolve_scope`'s
`isinstance(next_ceiling, TicketState)` and the enum-typed params would re-couple the engine to
`TicketState` membership and fail the string-id acceptance test. Taking `str` and calling
`str(state)` (a no-op for a `StrEnum`, an identity for a plain str) is the minimal change that keeps
existing callers passing `ticket.state` while admitting `"needs_zeta"`.

---

## 5. The `is` → `==` inventory (identity → value equality)

Every identity comparison on a `TicketState`/`FieldName` id becomes `==`. Reserved bookends may be
*named* literally (`TicketState.done`, `TicketState.dropped`, `TicketState.needs_kickoff`) but must
be compared with `==`. Full inventory, by file:line (verified against current source):

**resolution.py**
- :107 `machine.auto_accept_target(...) is not None` — **leave** (`is not None` on an Optional, not
  an id comparison).
- :153 `if ticket.state is TicketState.dropped:` → `== TicketState.dropped`.
- :165 `if field is machine.gating_field(ticket.state):` → `== machine.gating_field(...)`.
- :202 `if ticket.state is TicketState.dropped:` → `==`.
- :211 `if slot.proposal is not None:` — **leave** (`None` identity).
- :239 `if ticket.state in (TicketState.done, TicketState.dropped):` — **leave** (`in` uses `==`
  already; safe). (Not in the BRIEF list; noted for completeness — no change.)
- :252 `if field is None:` — **leave**.
- :259 `if slot.proposal is None:` — **leave**.
- :271 `if new_state is TicketState.dropped:` → `==`.
- :273 `if ticket.state is TicketState.dropped:` → `==`.
- :275 `if ticket.state is TicketState.needs_kickoff or new_state is TicketState.needs_kickoff:` →
  `== TicketState.needs_kickoff` on both (the direct-jump guard; keeps the reserved-bookend
  reference, de-identified — §6).
- :280 `if new_state is ticket.state:` → `==` (two ids compared).
- :288 `if ticket.state is TicketState.done:` → `==`.
- :292 `if ticket.state is TicketState.dropped:` → `==`.

**external_work.py** — **stays coding-bounded in t_tt01** (see the scope note below).
- :48 `if target_state not in WORKER_STATE_ORDER:` — this IS an engine decision reading a lifecycle
  constant. **Convert to the definition:** `if str(target_state) not in coding_bridge.views.ceiling_range(defn):`
  (with `defn = coding_bridge.coding_definition()`), keeping the exact `validation`/"external work
  target must be a linear ticket state"/`{"state": str(target_state)}` error. `ceiling_range` ==
  `WORKER_STATE_ORDER` for coding, so byte-identical. This lets external_work stop importing
  `WORKER_STATE_ORDER`.
- :54 `if ticket.state is TicketState.dropped:` → `==`.
- :56 `machine.state_index(target_state) < machine.state_index(ticket.state)` — **leave** (int
  compare, now via views).
- :113 `if target_state is not ticket.state:` → `!=`.
- :125 `ticket.ceiling is not target_state or ticket.at_cap is not AtCap.stop` → `!=` on both.
  (`AtCap.stop` is an enum value compared by identity today; `!=` is value-equal and safe.)
- :65, :90 `if slot.proposal is not None` / `if provided is not None or slot.value is not None` —
  **leave** (`None` identity).
- **`_PREFIX_COUNT` + `_FIELD_ORDER` (external_work.py:20-35) — DO NOT rebuild (t_tt03).** They stay
  literal coding tables. `_PREFIX_COUNT`'s keys are `TicketState` members and the lookup
  `_PREFIX_COUNT[target_state]` uses hash/`==`, so it is already value-correct for coding and needs
  no `is`→`==` change. `external_work.py` keeps importing `TicketState`/`FieldName` for these tables.

**Scope honesty (Codex-flagged):** because `_PREFIX_COUNT`/`_FIELD_ORDER` remain coding literals,
`external_work.decide_external_work` is **NOT wholly definition-driven after t_tt01** — its
prefix-reconciliation math is still coding-bounded (the BRIEF defers `SETTLED_PREFIX_INDEX` and the
prefix mechanism to t_tt03). t_tt01's external_work change is limited to: the target-eligibility
check (line 48 → `ceiling_range`) and the `is`→`==` conversions. The claim in acceptance #2 ("no
production caller reads lifecycle from the old constants for engine decisions") holds because the one
engine-decision read (line 48) is converted; the remaining `_PREFIX_COUNT` is a coding-specific
data table, not a read of the shared lifecycle-order constants, and is explicitly deferred.

**admission.py**
- :37 `if machine.is_terminal(state):` — **leave** (bool).
- :48 `if not machine.at_or_beyond_ceiling(...)` — **leave** (bool).
- :50 `if at_cap is AtCap.stop:` → `== AtCap.stop`.
- :61 `if field is not gating:` → `!= gating`.
- :74 **the recap-writability guard** — generalized, see §6.

**machine.py**
- :52 `is_terminal`: `state in (TicketState.done, TicketState.dropped)` — this whole body is
  **replaced** by delegation to `views.is_terminal` (§3 #2); no `is` survives.
- :83 `auto_accept_target`: `if field is not gating_field(state):` → `!= gating_field(state)`.
- :96/:121 `validate_ceiling`/`resolve_scope`: `not in WORKER_STATE_ORDER` — **replaced** by
  `ceiling_range` membership (`str(...) not in coding_bridge.views.ceiling_range(defn)`).
- :117 `resolve_scope`: `if not isinstance(next_ceiling, TicketState):` — **KEEP verbatim** (§3 #9,
  Codex-flagged). It is a *type* guard, not an id-identity comparison, and dropping it changes the
  coding error surface. Not part of the `is`→`==` inventory.
- :147 `plan_handoff_status`: `old_state is TicketState.needs_plan and new_state is
  TicketState.needs_implementation` → `==` on both; `implementer is Implementer.khushal` → `==`.

**data.py** — no `is`-on-id comparisons that gate engine decisions; the `is`/`is not`
`TicketStatus`/`_Unset`/`None` comparisons (e.g. :226, :441, :537) are status/sentinel/None
identity and stay. `ticket.state is TicketState.needs_kickoff` at data.py:663 and :676
(`take_over_ticket`/`release_ticket` kickoff guards) → convert to `==` (these ARE id comparisons on
the reserved bookend; keep the literal name, de-identify). **Add these two to the inventory** — they
are not in the BRIEF's list but are the same class and must be converted for the string-id invariant
to hold uniformly.

**readiness.py**
- :22 `machine.is_terminal(ticket.state)` — bool, leave. :29 `ticket.at_cap is AtCap.stop` →
  `== AtCap.stop`.

**fields_codec.py — `get_slot`/`with_slot` (lines 85-107): EXPLICITLY EXEMPT (do NOT convert).**
These dispatch a `FieldName` to a fixed `TicketFields` attribute via `field is FieldName.kickoff`
etc. (six identity checks + a `closeout` fallthrough). They must stay **`is`** because: (a) for
coding the `field` argument is always a `FieldName` (either a literal enum passed by callers, or the
`_as_field`-rewrapped enum from `gating_field`), so identity is correct and byte-identical; (b)
converting to `==` would make a *foreign* string field silently `==`-match `FieldName.closeout` via
`StrEnum` value-equality and mis-route it — strictly worse than the current fallthrough. The fixed
6-attribute struct is coding-only by design (t_tt01 does not build an N-ary slot map — see §1). So
the "exhaustive `is`→`==`" rule of §5 carries **one named exemption: the `get_slot`/`with_slot`
FieldName dispatch**, because those are slot-attribute routing on the fixed coding struct, not
workflow-id equality decisions. State this exemption in the "no lifecycle-constant reads" test's
sibling assertion so it is intentional, not an oversight.

**Acceptance for §9.C uses dynamically-constructed strings** (e.g. `"needs_" + "zeta"`) so Python
interning cannot mask a surviving `is` — a lingering `state is TicketState.x` would then be `False`
where `==` is `True`, failing the test.

---

## 6. The two coding-literal generalizations

### 6.1 `admission.check_recap_writable` (admission.py:73-79) — "past the first worker stage"
Today: `if state in (TicketState.needs_kickoff, TicketState.needs_success, TicketState.dropped):`.
Generalize to: recap is writable only **strictly past the first worker stage** (= past
`ceiling_range()[0]`, which is `default_ceiling`). Derive from the definition:

```python
def check_recap_writable(state: str, *, definition: WorkflowDefinition | None = None) -> None:
    defn = definition or coding_bridge.coding_definition()
    first_worker = coding_bridge.views.default_ceiling(defn)          # "needs_success" for coding
    if str(state) == defn.dropped_stage.id or not (
        machine.state_index(state, definition=defn) > machine.state_index(first_worker, definition=defn)
    ):
        raise PlannerError(ErrorCode.recap_too_early, "recap is writable only past needs_success",
                           {"state": str(state)})
```

- For coding: `needs_kickoff` (index 0) and `needs_success` (index 1) are NOT past index-1 → raise;
  `dropped` → raise (test `dropped` explicitly first, matching today's explicit `dropped` arm).
  `needs_approach`+ → allowed. **Byte-identical to today.**
- The message string stays `"recap is writable only past needs_success"` (test_tickets_engine.py:954
  asserts `ErrorCode.recap_too_early` and detail `{"state": ...}` but NOT the message text —
  grep-verified; keep the literal to avoid drift). Detail dict unchanged. `check_recap_writable` takes
  `str` + the optional `definition` seam (callers pass `ticket.state`, a `StrEnum`, unchanged).
- **Admission definition-threading policy:** the admission functions that call Tier-1 machine ops
  (`check_agent_proposal`, `check_recap_writable`) also take `*, definition=None` and forward it to
  their `machine.*` sub-calls, so a single passed definition governs the whole admission check (this
  is what makes the §10.C surviving-`is` probe realizable and what t_tt02 threads the row's type
  into). Existing callers omit it → coding. `check_agent_proposal` keeps its `state: TicketState`,
  `field: FieldName` param annotations (Tier-2 field-storage coupling; callers pass enums), forwarding
  `str(state)` inward.
- **Import note:** admission.py imports `machine` + adds `coding_bridge`. admission stays a leaf
  reaching `ticket_types` only through `coding_bridge`.

### 6.2 `resolution.decide_state_jump` direct-jump guard (resolution.py:275) — de-identify
Today: `if ticket.state is TicketState.needs_kickoff or new_state is TicketState.needs_kickoff:`.
Change to `==` on both (§5). Keep the reserved-bookend reference `TicketState.needs_kickoff`
literally (it is a universal reserved word, invariant 1); only the comparison operator changes. No
behavior change; message unchanged.

---

## 7. Single-writer preservation + the codec change

### 7.1 `_apply_decision` stays the sole canonical **state/ceiling** writer
**Precise invariant:** `_apply_decision` is the sole writer of `state`, `ceiling`, and `at_cap`. It
is NOT the sole writer of `fields` — `set_field_user_note` (data.py:883), `create_ticket`/
`create_ticket_from_external_work` (initial insert), and the recap paths also touch the row (recap
is a separate column). The single-writer guarantee this ticket must preserve is the one that already
exists: **canonical position (`state`/`ceiling`/`at_cap`) changes only through `_apply_decision`,
which is fed only by the resolution engine's `Decision`s.** The structural test (§10.D) asserts
exactly that, and NOT a false "sole fields writer" claim.

No structural change to `data._apply_decision` (data.py:107-149). It still reads `decision.new_state`
etc. and issues the one `UPDATE tickets SET fields=?, state=?, ceiling=?, at_cap=?`. It writes
`new_state.value` (data.py:132) — since `new_state` is a `TicketState` (re-wrapped by
`advance_target`/`decide_*`) for coding, `.value` works unchanged. **Guard:** `_accept_gating_proposal`
at resolution.py:78 does `assert isinstance(ceiling, TicketState)`. After §3 #9, `resolve_scope`
returns the incoming `next_ceiling` which for coding is a `TicketState` (the route/API parses it to
`TicketState` before calling — confirm via `_parse_next_ceiling`, api.py:295). So the assert holds
for coding. **Do not weaken this assert in t_tt01** (a `str`-typed ceiling is a t_tt02+ concern); if
the diff review shows the assert would fire under any existing test, that is a real regression to fix,
not to suppress.

### 7.2 The note-write path must validate the field against the definition
`data.set_field_user_note` (data.py:883-903) rewrites the whole fields JSON via
`fields_codec.with_slot(...)` + `fields_codec.fields_to_json(...)` **outside `_apply_decision`**
(the BRIEF's note-write path). It takes `field: FieldName`. **It is not safe by construction — but
the earlier rationale was factually wrong (review F3). The real current behavior for an unrecognized
field:**
- `get_slot` falls through and *reads* `closeout` (fields_codec.py:85-96) — it does NOT overwrite it.
- `with_slot` changes a slot only on exact `FieldName.closeout` identity; an unknown enum member
  changes **no slot** (fields_codec.py:99-107) — so the fields JSON is written back *unchanged*.
- `set_field_user_note` then appends a spurious `note_updated {"field": "<bogus>"}` event
  (data.py:901) despite nothing changing.
- And a **plain string** (not an enum) reaches `field.value` at data.py:901 → `AttributeError` →
  the transaction rolls back.

So the hazard is a spurious no-op event (foreign enum) or an `AttributeError` rollback (plain
string), NOT a silent `closeout` overwrite. The fix is the same controlled WRITE-path guard — reject
the undeclared field before touching any slot:

```python
defn = coding_bridge.coding_definition()
if not coding_bridge.has_field(defn, str(field)):
    raise PlannerError(ErrorCode.validation, "unknown ticket field", {"field": str(field)})
```

For coding this is a no-op (callers pass real `FieldName`s via the API's `FieldName` parse), so it is
parity-safe — every existing `set_field_user_note`/`set_note` test passes. It converts the
spurious-event / AttributeError paths into a clean, deterministic `PlannerError` and satisfies the
BRIEF's "validate field keys against the definition too." This is a controlled write-path validation
(unlike the read-side JSON-key tightening dropped in §7.3), so it carries no persisted-data risk.
Behavioral tests for it are required — §10.E.

### 7.3 `fields_codec.fields_from_json(raw, definition)` — decode declared fields, LENIENT on extras
Current signature `fields_from_json(raw: str) -> TicketFields` (fields_codec.py:70) hardcodes the six
keys and requires each present (lines 73-82). Change to:

```python
def fields_from_json(raw: str, definition: WorkflowDefinition | None = None) -> TicketFields:
    if definition is None:
        definition = coding_bridge.coding_definition()
    data = json.loads(raw); _require(isinstance(data, dict))
    # require each DECLARED field decodes; IGNORE unknown extra top-level keys (leniency preserved)
    for key in coding_bridge.field_ids(definition):
        _require(key in data)
    return TicketFields(kickoff=_slot_from_obj(data["kickoff"]), ... )  # unchanged construction
```

- **KEEP the good half of the codec change** (BRIEF-mandated): the codec is now parameterized by the
  definition and validates that the definition's DECLARED fields each decode (the existing per-slot
  `_slot_from_obj` shape checks stay). The "unsafe catch-all" the BRIEF calls out is the silent
  *default-construction of a missing declared slot* — that is dropped: a declared field absent from
  the JSON still `_require`-fails as today (no change from the current six-key requirement, now
  derived from the definition instead of a literal tuple).
- **DROP the reject-unknown-top-level-key tightening (review F4 — confirmed live-data risk).** The
  coordinator audited the real DBs: `data/planning.db` (current, 70 rows) is clean, but every
  `data/dogfood-*.db` and `data/ui-qa.db` carries a legacy top-level `result` key on every ticket
  row. `fields` is arbitrary `TEXT` with no shape guarantee (db.py:82), so **rejecting unknown extra
  top-level keys on READ would make those rows unreadable — a backward-incompatible break.** So
  `fields_from_json` stays **lenient on unknown extra top-level keys** (ignores them, exactly as
  today). No `"unknown ticket field key"` raise. Registry-driven field-shape validation of stored
  rows belongs to **t_tt02's startup integrity audit, paired with a migration that strips/normalizes
  legacy keys** — stated in §Out-of-scope so it is a named deferral, not a silent drop.
- **Legacy `{notes}` within-slot compat** is unchanged: `_slot_from_obj` reads
  `obj.get("user_note", obj.get("notes"))` (fields_codec.py:60). The existing legacy round-trip test
  (test_tickets_engine.py:123-141) passes unchanged.
- **The one-arg existing call** `fields_codec.fields_from_json(legacy)` (test_tickets_engine.py:133)
  keeps working via the `definition=None` default (resolves coding). **This default is the codec's
  leg of the bridge.**
  fields_codec imports **only `coding_bridge`** (sibling leaf) and calls `coding_bridge.field_ids(...)`
  — it must NOT `import ... from planner.ticket_types...` even under `TYPE_CHECKING`, because F6 walks
  the AST and would flag it as a second `ticket_types` importer (§3.1). The `WorkflowDefinition` type
  annotation is referenced via `coding_bridge.WorkflowDefinition` (re-exported by the bridge) or a
  string/`TYPE_CHECKING` alias that resolves through `coding_bridge`, never a direct `ticket_types`
  import. No cycle (`fields_codec → coding_bridge → ticket_types → tickets.contracts`, a sink).

- **Callers pass the definition explicitly** (production): `data._row_to_ticket` (data.py:77),
  `sprints/views.item_tickets` (views.py:116), `tickets/views.py:244` all call
  `fields_from_json(str(row["fields"]), coding_bridge.coding_definition())`. Passing the bridge
  definition is the BRIEF-mandated form; the `None` default exists solely so the pinned pure-call
  test survives.

---

## 8. The F6 guard-test change — the single unavoidable existing-test edit (flagged risk)

`test_no_production_module_imports_ticket_types` (test_ticket_type_registry.py:754-763) asserts NO
production module imports `ticket_types`. **After this ticket, exactly one production module —
`planner.tickets.logic.coding_bridge` — imports `ticket_types` (by design; it is the seam).** F6 as
written will fail.

This is a genuine tension with "zero edits to existing assertions." It is NOT a coding-literal move;
it is a t_tt00 invariant ("Phase 0 additive-only, nothing consumes the registry yet") that t_tt01 is
defined to retire. The BRIEF's escape hatch applies: *"if any existing test encodes a [constant] that
must move, name it and propose the minimal faithful change, flagged as a risk."*

**Minimal faithful change (the ONLY existing-test edit in this ticket):** narrow F6 from "no
production module imports ticket_types" to "no production module imports ticket_types **except the
single designated seam** `planner/tickets/logic/coding_bridge.py`." Concretely, in
`test_no_production_module_imports_ticket_types`, skip the one allowed seam path:

```python
_ALLOWED_TICKET_TYPES_IMPORTER = _PLANNER_ROOT / "tickets" / "logic" / "coding_bridge.py"
...
for path in _PLANNER_ROOT.rglob("*.py"):
    if _TICKET_TYPES_ROOT in path.parents or path == _TICKET_TYPES_ROOT:
        continue
    if path == _ALLOWED_TICKET_TYPES_IMPORTER:
        continue          # the single sanctioned seam (t_tt01); every other module reaches
                          # the registry only through it
    ...
```

**But a bare skip only proves "no importer except possibly this path" — it does not assert the seam
is the ONLY importer.** To keep the guard's teeth AND assert "exactly one, named," collect the full
set of production importers and assert equality:

```python
def test_only_the_single_seam_imports_ticket_types() -> None:  # was F6
    importers = set()
    for path in _PLANNER_ROOT.rglob("*.py"):
        if _TICKET_TYPES_ROOT in path.parents or path == _TICKET_TYPES_ROOT:
            continue
        anchor = _anchor_package_for(path)
        if _file_imports_ticket_types(ast.parse(path.read_text(), filename=str(path)), anchor):
            importers.add(path)
    assert importers == {_ALLOWED_TICKET_TYPES_IMPORTER}   # exactly the coding_bridge seam
```

This **strengthens** the guard: it fails if a *second* module imports `ticket_types` (the scatter to
prevent) AND fails if the seam itself somehow stops importing it. The invariant moves from "zero
importers" to "exactly `{coding_bridge}`" — the true post-t_tt01 architecture. Surface this as the
one sanctioned assertion change; do not touch any other assertion in the file (F3 and all
parity/negative tests stay verbatim).

**Alternative considered and rejected:** route the bridge through a NEW non-`ticket_types` module
that `ticket_types` itself exposes — impossible without `ticket_types` importing outward past its
two allowed leaves (F3 forbids). There is no way to consume the registry without *some* production
module importing `ticket_types`; F6 must relax. The single-seam narrowing is the smallest honest
change.

---

## 9. Per-call-site threading table

For each caller: which machine/codec functions it calls, and the exact change. The parameterized ops
carry `*, definition=None` and **default** to `coding_bridge.coding_definition()` when omitted — so
existing callers pass no definition and get coding (parity); the change is that the seam EXISTS (an
explicit definition can be passed), not that callers must thread it now. "pass defn" = this call site
already passes the definition explicitly (the codec's production callers).

| Caller file | Calls | Change |
|---|---|---|
| `tickets/logic/machine.py` | (defines all 12) | Import `coding_bridge`; drop 4 constants + `FIELD_GATES`; Tier-1 fns take `*, definition=None` + overloads and delegate to `coding_bridge.views.*`; Tier-2 (`resolve_scope`/`validate_ceiling`/`has_pending_*`) stay enum-typed, `defn`-resolved; add `_as_state`/`_as_field`; `is`→`==` (§5). Recap/jump generalizations live in admission/resolution. |
| `tickets/logic/resolution.py` | `machine.auto_accept_target`, `.gating_field`, `.advance_target`, `.resolve_scope`, `.field_is_passed` | Omits `definition=` → coding default (parity). Apply `is`→`==` inventory (§5). `_accept_gating_proposal` assert unchanged (§7.1). |
| `tickets/logic/admission.py` | `machine.is_terminal`, `.gating_field`, `.at_or_beyond_ceiling` | Import `coding_bridge`. `check_agent_proposal`/`check_recap_writable` gain `*, definition=None` and forward it to their `machine.*` sub-calls (§6.1); existing callers omit it → coding. Param annotations stay `state: TicketState`/`field: FieldName` (Tier-2 coupling). `is`→`==` at :50,:61. |
| `tickets/logic/external_work.py` | `machine.state_index` | Line 48: `WORKER_STATE_ORDER` membership → `coding_bridge.views.ceiling_range(defn)` (drops the `WORKER_STATE_ORDER` import). `is`→`==` at :54,:113,:125. Leave `_PREFIX_COUNT`/`_FIELD_ORDER` (t_tt03) — keep the `TicketState`/`FieldName` import for those. Import `coding_bridge`. No new arg. |
| `tickets/logic/fields_codec.py` | (defines `fields_from_json`) | New optional `definition` param defaulting to bridge (§7.3); import **only `coding_bridge`** (never `views`/`ticket_types` directly, incl. `TYPE_CHECKING` — §3.1); call `coding_bridge.field_ids(...)`; validate declared fields decode; **stay LENIENT on unknown extras** (F4 — do NOT reject). |
| `tickets/data.py` | `machine.plan_handoff_status`, `.gating_field`; `fields_codec.fields_from_json`, `.fields_to_json`, `.get_slot`, `.with_slot`; `resolution.decide_*`; `external_work.decide_external_work`; `admission.*` | `_row_to_ticket` passes `coding_bridge.coding_definition()` to `fields_from_json` (data.py:77). `set_field_user_note` adds the undeclared-field guard (§7.2). `is`→`==` at :663,:676 (kickoff guards). `plan_handoff_status`/`gating_field` calls omit `definition=` → coding default. **No new required param on any `data.*` public writer** (parity constraint A) — the `*, definition=None` seam is on the pure engine ops, not the `data.*` writers, whose ~130 pinned call sites stay untouched. Import `coding_bridge`. |
| `runtime/readiness.py` | `machine.is_terminal`, `.has_pending_parked_proposal`, `.gating_field`, `.at_or_beyond_ceiling` | Omits `definition=` → coding default. `is`→`==` at :29 (`at_cap is AtCap.stop`). |
| `runtime/employee_step_runner.py` | `machine.gating_field` | Passes `ticket.state` (a `TicketState`) → `gating_field` overload narrows to `FieldName | None` → `gating.value` unchanged (§4). Omits `definition=` → coding. |
| `sprints/views.py` | `machine.has_pending_gating_proposal`; `fields_codec.fields_from_json` (views.py:116) | Pass `coding_bridge.coding_definition()` to `fields_from_json`. `has_pending_gating_proposal(TicketState(state), fields)` call unchanged (still one call, `TicketState` is a str). Import `coding_bridge`. |
| `tickets/views.py` | `machine.has_pending_gating_proposal`; `fields_codec.fields_from_json` (views.py:244) | Pass bridge definition to `fields_from_json`. `has_pending_gating_proposal(TicketState(state), fields)` unchanged. Import `coding_bridge`. Also `board_json` iterates `for s in STATE_ORDER` (views.py:277) — **leave** (read-model column order; it reads the *constant*, not the engine; the BRIEF forbids only `machine.py` reading the constants, and only for engine decisions — a read-model column ordering is not an engine decision. Confirm with the integrator; if desired it can later read `views.stage_ids`, but that is out of scope for parity here). |

**Note:** `sprints/views.py` and `tickets/views.py` import `coding_bridge` (in `tickets.logic`) —
an inward import from `sprints`/`tickets`, no cycle (`sprints.views → tickets.logic.coding_bridge →
ticket_types → tickets.contracts`, a sink).

---

## 10. Tests

### Parity (the proof) — NO edits to existing assertions
Run the full existing suite through `./verify`. Every existing assertion in
`test_tickets_engine.py`, `test_value_edit_logic.py`, `test_ticket_lifecycle.py`,
`test_ticket_edit_api.py`, `test_readiness_actions.py`, `test_chief_external_work.py`,
`test_flows_a.py`, `test_ticket_type_registry.py` (F3 + parity/negative) stays verbatim and passes.
**The one exception is F6** (§8): narrowed to allow the single seam module — flagged, not silent.

### New tests (add a file `tests/unit/test_engine_parameterization.py`)

**A. No lifecycle-constant reads in machine.py** (AST/grep assertion):
- Parse `src/planner/tickets/logic/machine.py`; assert its import set contains **none** of
  `STATE_ORDER`, `WORKER_STATE_ORDER`, `GATING_FIELD`, `ADVANCE_TARGET` (and no local `FIELD_GATES`
  definition). Assert it imports `coding_bridge`.
- Assert (grep) no production module reads lifecycle from those constants *for engine decisions*:
  scope the check to `machine.py` importing none of them (the strong, mechanical form). The
  read-model `STATE_ORDER` use in `tickets/views.py:277` is out of scope (column order, §9 note).

**B. Codec — definition-validated decode, LENIENT on extras (per review F4)**:
- **Lenient on unknown extras (the live-data guarantee):**
  `fields_from_json(json.dumps({...six coding slots..., "result": "legacy value", "audit": {...}}),
  coding_definition())` **does NOT raise** — it returns a `TicketFields` decoding the six declared
  slots and ignoring `result`/`audit`. This pins the F4 backward-compat requirement (legacy
  `dogfood-*`/`ui-qa` rows carry a top-level `result` key). Assert the six slots decode correctly and
  no exception is raised.
- **Declared-field requirement (the good half kept):**
  `fields_from_json(json.dumps({...five of six declared slots, "closeout" MISSING...}),
  coding_definition())` raises `PlannerError(code=validation)` ("corrupt ticket fields JSON" — the
  existing `_require` message, unchanged) — the definition's declared fields must each be present.
- `fields_from_json(<valid six-key JSON>, coding_definition())` round-trips: returned `TicketFields`
  equals the input slot-for-slot (value/proposal/user_note); `fields_to_json(parsed)` re-serializes
  the six keys.
- `fields_from_json(<valid six-key JSON>)` (ONE arg, default) returns the same result — pins the
  bridge default that keeps the pinned pure-call test alive.

**C. String-id capable (not enum-bound)** — the core N-ary proof:
- Build a **synthetic** `WorkflowDefinition` with **middle stage ids NOT in `TicketState`**,
  honoring the registry validation rules (R4 first=`needs_kickoff` gating `kickoff`; R5 last=`done`;
  R13 first field=`kickoff`): stages `needs_kickoff → <A> → <B> → done` where
  `<A> = "".join(("needs_", "alpha"))` and `<B> = "".join(("needs_", "beta"))`. **Use `"".join(...)`,
  NOT `"needs_" + "alpha"`** — CPython constant-folds an adjacent-literal `+` at compile time into a
  single interned literal, which would NOT defeat interning; `"".join((...))` builds a genuinely
  distinct runtime `str` object (Codex-flagged). Fields `(kickoff, <fA>, <fB>)` with
  `<fA> = "".join(("al","pha"))`, `<fB> = "".join(("be","ta"))` gating `<A>`/`<B>`; a valid worker
  profile (`specialist_skill` in a test `known_skills`, `toolset_profile` in test
  `known_toolset_profiles`). Register via `build_registry([SYNTHETIC], known_skills=...,
  known_toolset_profiles=...)` in the test (never the production bridge). Confirmed: `needs_alpha` and
  `needs_beta` are absent from `TicketState` (contracts.py:18-27), so `_as_state`/`_as_field` return
  bare strings for them.
- **Probe the views directly** (they are the definition-taking layer) with the synthetic def:
  `views.state_index(defn, <A>) == 1`, `views.advance_target(defn, <A>) == <B>` and
  `views.advance_target(defn, <B>) == "done"`, `views.gating_field(defn, <A>) == <fA>`,
  `views.is_terminal(defn, "done") is True`, `views.is_terminal(defn, <A>) is False`,
  `views.ceiling_range(defn) == (<A>, <B>, "done")`, `views.default_ceiling(defn) == <A>`.
- **Probe the machine (Tier-1) functions by passing `definition=SYNTHETIC` explicitly** — no
  monkeypatch needed. This is the whole point of the F1 optional-`definition` seam: it proves a
  SECOND definition flows through the same engine functions concurrently with coding (which
  monkeypatching `coding_definition` could NOT prove — it would only swap the single global). Assert
  the engine operates on the **non-enum** ids:
  - `machine.advance_target(<A>, definition=SYNTHETIC)`: `_as_state(<B>)` returns the **bare string
    `<B>`** (`TicketState("needs_beta")` raises), so the result is `is not TicketState.done` and
    matches no enum member. Assert `type(...) is str` and `== <B>`. (Probing the non-enum MIDDLE
    target is what detects a surviving identity comparison — `advance_target(...) == "done"` could
    not, since `_as_state("done")` returns `TicketState.done`.)
  - `machine.gating_field(<A>, definition=SYNTHETIC)` returns bare `<fA>` (`_as_field` bare for a
    non-enum field): `== <fA>`, `type(...) is str`.
  - `machine.is_terminal(<A>, definition=SYNTHETIC) is False`,
    `machine.is_terminal("done", definition=SYNTHETIC) is True`,
    `machine.state_index(<A>, definition=SYNTHETIC) == 1`,
    `machine.at_or_beyond_ceiling(<A>, <A>, definition=SYNTHETIC) is True`.
  - **Concurrency proof (the F1 guarantee):** in the SAME test, without any patch,
    `machine.advance_target(TicketState.needs_success)` (no `definition=`) still returns
    `TicketState.needs_approach` via the coding default — proving coding and SYNTHETIC flow through
    the engine simultaneously, each governed by its own passed/defaulted definition. This is the
    capability t_tt02 depends on and that an internal-only accessor would have blocked.
- **Surviving-`is` detection:** drive an **enum-independent** guard on the dynamically-built strings
  — NOT `decide_state_jump` (its `_state_change` calls `.value`; its guards compare the reserved
  bookends, not the middle ids). If `check_agent_proposal` gains a `*, definition=None` param
  (Tier-1-adjacent — see §6.1/§9 for admission's definition threading), drive
  `admission.check_agent_proposal(state=<A>, ceiling=<A>, at_cap=AtCap.propose, field=<fA>,
  definition=SYNTHETIC)`: it calls `is_terminal`/`gating_field`/`at_or_beyond_ceiling` and compares
  `field != gating` (was `is not`, §5). With `<A>`/`<fA>` distinct runtime strings, a surviving
  `field is not gating` would wrongly reject a valid proposal (the `_as_field(<fA>)` bare string is a
  different object from the `field` arg), failing the assertion. If admission is NOT
  definition-parameterized in t_tt01, fall back to asserting the same on the machine layer directly
  (`machine.gating_field(<A>, definition=SYNTHETIC) == <fA>` where a surviving `is` in
  `auto_accept_target`'s `field != gating_field(...)` is probed via
  `machine.auto_accept_target(<A>, <A>, <fA>, definition=SYNTHETIC) == <B>`). Because `<A>`/`<fA>`
  are built via `"".join`, interning cannot mask a surviving `is`.

**E. Note-path behavioral tests (review F3) — the write-path field guard**:
- **Undeclared note field rejected, no side effect:** call `data.set_field_user_note(conn, t.id,
  field=<a foreign field>, user_note="x", actor="human", now=now)` and assert it (1) raises the
  specified `PlannerError(code=validation, message="unknown ticket field", detail={"field": ...})`;
  (2) changes NO fields, state, ceiling, or scope (read the ticket back, assert every slot
  value/proposal/user_note and state/ceiling/at_cap are byte-identical to before); (3) appends NO
  event (assert the event count for the ticket is unchanged — proving the guard fires before the
  `note_updated` append and the transaction rolls back). (Passing a foreign field requires
  constructing one that reaches `set_field_user_note` past the API's `FieldName` parse — call the
  `data` layer directly with a non-declared `FieldName`-typed value, or the raw string via the data
  function, per how the guard is written.)
- **Valid note changes only `user_note`:** `set_field_user_note(field=FieldName.approach, ...)`
  changes ONLY `fields.approach.user_note`, preserving every other slot's value and proposal and the
  approach slot's own `value`/`proposal`; asserts exactly one `note_updated {"field": "approach"}`
  event. (Pins that the added guard did not regress the happy path.)

**F. Strict-mypy overload cases (review F2)** — a `typing`-only module under the CI mypy gate (or a
`assert_type`/`reveal_type` test) proving the overloads resolve as claimed under `strict = true`:
- `assert_type(machine.advance_target(TicketState.needs_success), TicketState)` and
  `assert_type(machine.advance_target(a_str), TicketState | str)`.
- `assert_type(machine.gating_field(TicketState.needs_success), FieldName | None)`.
- `machine.has_pending_gating_proposal(a_TicketState, fields)` type-checks (feeds `FieldName | None`
  into `get_slot`), and `resolve_scope(a_str_new_state, ...)` is a **mypy error** (Tier-2 stays
  `TicketState`-typed → a bare `str` cannot reach `ScopePair.next_ceiling`). These two are the exact
  spots the first two self-reviews mis-typed; the case file makes the constraint machine-checked.

**D. Single-writer structural test**:
- Static assertion (AST over `src/planner/tickets/data.py`): the SQL substring
  `"state ="` / `"ceiling ="` / `"at_cap ="` in an `UPDATE tickets` statement appears **only inside
  `_apply_decision`** (the sole canonical writer). Every other `UPDATE tickets` in data.py touches
  only `recap`, `ticket_status`, `chat_session_key`, `sprint_item_id`, `sprint_id`, `project_id`,
  `fields`, `updated_at`, or plain-attribute columns — never `state`/`ceiling`/`at_cap`. This
  mirrors the existing single-writer guarantee and catches a regression where a new path writes
  canonical position outside `_apply_decision`. (If a cleaner behavioral form is preferred: drive a
  transition and assert only `_apply_decision`'s UPDATE changed state — but the static form is the
  faithful structural guard the BRIEF asks for.)

### Codex review (per project pipeline)
After the plan is approved and before implementing, and again on the implementation diff, run
`/codex-cli` (`--sandbox read-only`, reasoning-effort high) pointed at:
`machine.py`, `resolution.py`, `admission.py`, `external_work.py`, `fields_codec.py`, `data.py`,
`coding_bridge.py`, the three view callers, plus `views.py`/`registry.py` and this plan — asking
specifically: (1) any `is`-on-id survivor; (2) any `coding` behavior drift (event payloads, error
codes, state order); (3) the enum-return identity for `advance_target` (all THREE assertions);
(4) the codec staying LENIENT on unknown extras + declared-field decode + legacy `{notes}` compat
(F4 — must NOT reject extras); (5) the no-cycle claim; (6) the single F6 narrowing; (7) that the
optional-`definition` seam genuinely lets two definitions flow at once (F1); (8) strict-mypy clean
under the overloads (F2).

---

## 11. Order of implementation (single serial worktree; all files overlap)
1. `coding_bridge.py` (new leaf; singleton registry + injected catalogs + `views`/`WorkflowDefinition`
   re-export + thin `field_ids`/`has_field` wrappers). `_as_state`/`_as_field` live in machine, not here.
2. `machine.py` (Tier-1 `*, definition=None` + overloads + `_as_state`/`_as_field` + `is`→`==` + drop
   constants; Tier-2 `resolve_scope`/`validate_ceiling`/`has_pending_*` stay enum-typed via `defn`).
3. `fields_codec.py` (definition-parameterized decode of declared fields; **LENIENT on unknown
   extras** per F4; bridge `definition=None` default).
4. `admission.py` (recap generalization + `check_agent_proposal`/`check_recap_writable` `*,
   definition=None` threading + `is`→`==`).
5. `resolution.py`, `external_work.py` (`is`→`==`; jump-guard de-identify; external_work line-48 →
   `ceiling_range`; thread `definition` where they call Tier-1 ops).
6. `data.py` (codec-defn pass in `_row_to_ticket`; `set_field_user_note` undeclared-field guard;
   kickoff-guard `is`→`==`).
7. `readiness.py`, `sprints/views.py`, `tickets/views.py`, `employee_step_runner.py` (codec-defn
   pass + `is`→`==`).
8. `core/server.py` (eager warm `coding_registry()` in lifespan).
9. New tests (§10 A–F); F6 narrowing (§8).
10. Full `./verify` + strict mypy; Codex diff review; integrate.

---

## Out of scope (named deferrals, not silent drops)
- **Codec strictness on unknown top-level JSON keys (F4).** `fields_from_json` stays lenient on
  unknown extras in t_tt01 — legacy `data/dogfood-*`/`ui-qa` rows carry a top-level `result` key and
  would become unreadable. Registry-driven field-shape validation of stored rows → **t_tt02 startup
  integrity audit + a migration that strips/normalizes legacy keys.**
- **Genericizing `ScopePair`/`TicketFields` to string-native (Tier 2).** `resolve_scope`,
  `validate_ceiling`, `has_pending_gating_proposal`, and the codec slot map stay coding-bound
  (`TicketState`/`FieldName`-typed) — making them string-native now fails strict mypy and crashes
  `_apply_decision`. → **t_tt02x/t_tt02**, when `probe` actually stores fields.
- **`external_work`'s `_PREFIX_COUNT`/`_FIELD_ORDER` prefix-reconciliation map.** Stays a coding
  literal table (only the line-48 target-eligibility check moves to `ceiling_range`). → **t_tt03.**
- **`tickets/views.py:277` `board_json` `for s in STATE_ORDER`.** Read-model column ordering, not an
  engine decision; left reading the constant. Optional later move to `views.stage_ids`.

---

## 12. Open risks (sharpest three)

1. **F6 guard-test conflict (highest).** `test_no_production_module_imports_ticket_types` forbids the
   exact thing t_tt01 must do. The plan narrows it to one named seam (§8) — a real, if minimal,
   existing-test change. If the owner insists on truly zero test edits, there is **no way to consume
   the registry** (F3 blocks the reverse routing). Must be accepted as the one sanctioned change.
2. **Strict-mypy under the overloads (review F2).** Parity + type-honesty hinge on the
   `advance_target`/`gating_field`/`auto_accept_target` overloads narrowing a `TicketState` arg to the
   enum return, while `resolve_scope`/field-storage stay `TicketState`/`FieldName`-typed (Tier 2). If
   the repo's strict-mode overload matching does not narrow as designed, the fallback is a
   `cast(TicketState, ...)` at resolution.py:53/:166 only — never widening `Decision`/`ScopePair`. The
   §10.F mypy-case module is the gate; the three `advance_target(...) is TicketState…` assertions
   (test_ticket_lifecycle.py:120,121,123) are the runtime gate.
3. **The optional-`definition` seam must be honored, not "simplified" back to internal-only (review
   F1).** The engine's parameterized ops take `*, definition=None` and resolve
   `definition or coding_bridge.coding_definition()`; existing fixed-arity callers omit it (parity),
   new callers pass it (N-ary). An integrator who "simplifies" this to resolve `coding_definition()`
   internally with no override would silently make the engine single-definition and force t_tt02 to
   re-refactor these same signatures — the concurrency proof in §10.C exists specifically to catch
   that regression. Secondary: the Tier-2 `isinstance(next_ceiling, TicketState)` /
   `assert isinstance(ceiling, TicketState)` couplings are intentional coding-bound spots for t_tt02+
   to revisit — do NOT "fix" them here.
