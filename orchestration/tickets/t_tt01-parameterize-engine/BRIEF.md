# Ticket t_tt01 — Parameterize the state-machine engine by an explicit workflow definition

Phase 1 of `orchestration/ticket-types-redesign/PLAN.md`. Make the state-machine math read the workflow
from a **`WorkflowDefinition`** (the registry that t_tt00 built) instead of the module-global lifecycle
constants — while `coding` behavior stays **byte-identical**. This is the load-bearing refactor: it touches
the correctness core (`machine.py`, `resolution.py`, `admission.py`, the codec) and every caller.

## Goal (approved success)

The engine is **definition-driven and string-id-native**:
- Every state-machine function takes an explicit `WorkflowDefinition` (or resolves one) and reads the
  workflow — stage order, gates, advance, ceiling range, terminals, transition effect — from the
  registry's derived views (`planner.ticket_types.logic.views`, the drop-ins t_tt00 built to match each
  `machine.py` op), never from `STATE_ORDER`/`WORKER_STATE_ORDER`/`GATING_FIELD`/`ADVANCE_TARGET`/
  `FIELD_GATES`.
- It operates on **string state/field ids**, not `TicketState`/`FieldName` enum membership. `coding`'s
  ids happen to equal the enum values (so parity holds and existing callers keep passing `ticket.state`,
  which is a `StrEnum` and already a `str`), but the engine must not *require* a state to be an enum
  member — a future type's stage like `"needs_alpha"` (not in `TicketState`) must be able to flow through
  the same functions. This is what unblocks `probe` (t_tt02x) and the go/no-go gate.
- **`coding` parity:** the entire existing ticket-engine / resolution / admission / readiness / external-
  work test suite passes **UNCHANGED**. No test edits to make them pass (editing an existing assertion to
  accommodate a behavior change is a parity failure — surface it instead).

## The pre-persistence bridge (why this is safe before t_tt02)

`ticket_type` is not a DB column until t_tt02, so **every production caller passes
`registry.require("coding")`** in this ticket. The codec becomes `fields_from_json(raw, definition)` —
resolved from the passed definition, NOT from a `ticket.ticket_type` that doesn't exist yet. t_tt02
removes the bridge by resolving the definition from each row's stored type. (If this ticket instead
resolved the definition from `ticket.ticket_type`, t_tt02 would have to precede t_tt01 — the bridge is
what keeps the phase order safe. From the plan review.)

The registry instance must be reachable by callers. Decide and implement the seam: a single composition-
root-built `Registry` (built with the injected catalogs) passed/available to the engine call sites. Do
**not** import `minds/config`; catalogs are injected (see D102 / t_tt00). The planner names the exact
wiring (a module-level singleton built at import of a leaf, or threaded through the call path) with the
no-cycle property preserved and stated.

## Scope — files and what changes

**In:**
- `tickets/logic/machine.py` — the 12 functions (`state_index`, `is_terminal`, `gating_field`,
  `field_is_passed`, `advance_target`, `auto_accept_target`, `at_or_beyond_ceiling`, `validate_ceiling`,
  `resolve_scope`, `has_pending_gating_proposal`, `has_pending_parked_proposal`, `plan_handoff_status`)
  become definition-parameterized, delegating to the registry views. `machine.py` imports **neither**
  `STATE_ORDER` nor `WORKER_STATE_ORDER` nor the gate/advance/FIELD_GATES tables afterward.
- The callers of those functions thread the definition: `tickets/logic/resolution.py`,
  `tickets/logic/admission.py`, `tickets/logic/external_work.py`, `tickets/data.py`,
  `tickets/views.py`, `sprints/views.py`, `runtime/readiness.py`, `runtime/employee_step_runner.py` —
  each passing `registry.require("coding")` (the bridge).
- `tickets/logic/fields_codec.py` — `fields_from_json` becomes a **validated mapping over the
  definition's declared fields**; drop the unsafe catch-all default that silently accepts unknown keys.
- **Identity → value equality (`is` → `==`).** Per-type states/fields are strings; identity breaks.
  Replace across the logic — at minimum: `resolution.py:153/165/202/271/273/275/280/288/292`,
  `external_work.py:54/113`, `admission.py:61`, `machine.py` (any surviving `is`), and any others found.
  Reserved bookends (`done`/`dropped`/`needs_kickoff`) may still be **named** literally, but compared with
  `==`. Acceptance must use dynamically-constructed-but-equal strings so interning can't mask a surviving
  `is`.
- **Generalize the coding-specific literals kickoff dragged in:** the recap-writability guard
  (`admission.py:74`, currently `state in (needs_kickoff, needs_success, dropped)`) generalizes to "recap
  writable only past the **first worker stage**" (derived from the definition's ceiling range); the
  direct-jump guard (`resolution.py:275`, naming `needs_kickoff`) keeps the reserved-bookend reference but
  de-identifies it. `external_work.py`'s `SETTLED_PREFIX_INDEX` (coding-state→index map) is a **t_tt03**
  concern — do NOT rebuild it here; leave it working for coding (it may keep its literal map for now).

**Out (do not touch):**
- No DB column / migration (t_tt02). No CLI/API/manifest wiring (t_tt03). No new type (t_tt02x). Do not
  modify `src/planner/ticket_types/` (t_tt00 is done and reviewed — consume it, don't change it).
- Do **not** delete `STATE_ORDER`/`WORKER_STATE_ORDER`/`GATING_FIELD`/`ADVANCE_TARGET`/`FIELD_GATES` from
  `tickets/contracts.py`/`machine.py` module namespace — t_tt00's golden parity test imports them as the
  spec the `coding` definition is proven equal to. `machine.py` simply stops *reading* them.
- Do not change `external_work.py`'s prefix map semantics (t_tt03).

## The load-bearing design question (planner must resolve explicitly)

**Enum vs string id at the machine boundary.** Today `machine.py` is typed on `TicketState`/`FieldName`
and uses identity/`.index()` on enum tuples. The end state is string-id-native. Since `TicketState`/
`FieldName` are `StrEnum`s, an enum instance already *is* a `str`, so callers can keep passing
`ticket.state` while the engine treats it as a string id and delegates lookups to the registry views
(which take `str`). Resolve: exact signatures (do machine fns take `str` ids, or keep `TicketState` params
that are immediately `.value`-normalized?), how `TicketFields`/`FieldSlot` access works when field ids are
strings (the codec + `fields_codec.get_slot`), and how the single-writer `_apply_decision` (`data.py`)
carries the definition. The invariant: `_apply_decision` stays the sole canonical state/ceiling/fields
writer; the resolution engine stays the single door.

## Acceptance (concrete, from the plan review)

1. **Parity:** the full existing suite (`./verify`) passes with `coding` routed through the registry,
   with **zero edits to existing test assertions**. This is the proof.
2. **No lifecycle-constant reads:** an assertion (grep/AST) that `machine.py` imports neither `STATE_ORDER`
   nor `WORKER_STATE_ORDER` nor `GATING_FIELD`/`ADVANCE_TARGET`/`FIELD_GATES`, and that no production
   caller reads lifecycle from those constants for engine decisions; every temporary definition resolution
   is exactly `registry.require("coding")`.
3. **String-id capable:** a unit test that the engine functions operate correctly on a **synthetic
   definition with a stage id NOT in `TicketState`** (e.g. a throwaway 3-stage def with `"needs_zeta"`) —
   proving the engine is not enum-bound. (Minimal; the full N-ary proof is t_tt02x/the gate.) Use a
   dynamically-constructed string so a surviving `is` fails this test.
4. **Codec:** `fields_from_json(raw, definition)` round-trips coding fields identically and **rejects** an
   unknown field key (asserted error) instead of silently defaulting.
5. **Single-writer intact:** a structural test that state/ceiling/fields updates still flow only through
   `_apply_decision`.

## References
- Plan: `orchestration/ticket-types-redesign/PLAN.md` — Phase 1, invariants 1 & 9, "Registry validation —
  the enforcement doors", "Transition-effect semantics".
- Registry API to consume: `src/planner/ticket_types/logic/views.py` + `registry.py` (built in t_tt00 —
  `state_index`, `is_terminal`, `gating_field`, `gated_state`, `advance_target`, `advance_map`,
  `ceiling_range`, `default_ceiling`, `linear_terminal_stage_id`, `transition_effect`, `require`).
- Current engine: `tickets/logic/machine.py`, `resolution.py`, `admission.py`, `fields_codec.py`,
  `external_work.py`; `tickets/data.py` (`_apply_decision`).
- Standing rules: `PRINCIPLES.md` (single writer, one door), `CLAUDE.md`, `decisions.md` D102.
