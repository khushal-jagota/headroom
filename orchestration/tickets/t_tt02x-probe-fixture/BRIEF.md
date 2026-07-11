# Ticket t_tt02x — The `probe` fixture type + engine drive-to-done (the genericity proof)

Phase 2.5. Define a **minimal synthetic second type `probe`**, registered **only in tests**, and drive a
`probe` ticket through the full persistence + engine stack to `done` via real propose/accept — proving the
machine is genuinely N-ary (not `coding` in disguise). Production still ships **coding-only**. This is the
canonical fixture reused by t_tt03's go/no-go gate and t_tt05.

Depends on **t_tt02b** (generic field storage + Tier-2 scope + threaded `decide_*`), so `probe`'s
`alpha`/`beta` fields and `needs_alpha` ceiling actually flow.

## The `probe` definition (a deliberately different shape from coding)

- **Stages:** `needs_kickoff` (gates `kickoff`) → `needs_alpha` (gates `alpha`) → `needs_beta` (gates
  `beta`) → `done`. `dropped` is the reserved exceptional terminal. Shares the universal
  `needs_kickoff`/`kickoff` prefix and the `done` bookend; the middle stages/fields are `probe`-specific
  and are **not** `TicketState`/`FieldName` members (proving the engine isn't enum-bound).
- **Fields (ordered):** `kickoff`, `alpha`, `beta`.
- **Ceiling range:** `needs_alpha → needs_beta → done`; **default ceiling `needs_alpha`** (its first worker
  stage — NOT `needs_success`; this alone proves per-type default-ceiling derivation).
- **`supports_prefix_reconciliation`:** `True` (consumed by t_tt03 external-work).
- **Worker profile:** a placeholder — `specialist_skill` a placeholder id, `model`/`reasoning_effort`
  None, `toolset_profile` `"default"`. Inert until t_tt05; validated only for reference integrity (add
  the placeholder skill/toolset to the test catalogs).
- **Transition hook:** none required (or one trivial hook only if needed to exercise the hook machinery —
  do not add speculatively).

Registered via a **test-only registry** (`coding_bridge.set_registry_for_test` from t_tt02, or a test
registry builder) — NOT in production `coding_registry()`. A test asserts production offers only `coding`.

## Scope

**In:**
- The `probe` `WorkflowDefinition` (a test fixture module, e.g. `tests/support/` or a conftest helper).
- **Contract tests:** the registry validates `probe`; its serialized manifest is asserted **exactly**
  (stages/labels/gates/advance/fields/ceiling_range/default_ceiling/worker_profile_id); golden gate-map +
  field-order; `default_ceiling == "needs_alpha"`; production registry contains only `coding`.
- **Engine drive-to-done (the mini go/no-go, DATA-layer):** with `probe` test-registered, `create_ticket(
  ticket_type="probe")`; then at every non-terminal stage file the gating proposal (with recap) and
  accept it, driving `needs_kickoff → needs_alpha → needs_beta → done` through the **real** `data.*`
  writers (`file_proposal`/`accept_proposal`) and `resolution.decide_*` — asserting at each step: exact
  state, ceiling, the field the proposal parks on (the registry-selected gate), accepted value, cleared
  proposal, and the emitted event order. Assert **no worker turn / session** was created. Also drive one
  `probe` ticket to `dropped` and assert terminal handling.
- **Negative cases:** an invalid `probe` state / field / ceiling and a create with an unknown type each
  raise the exact validation code.

**Out:** CLI/API ingress + external-work create/reconcile for `probe` (that's the **go/no-go gate at
t_tt03**). Worker realization (t_tt05 — the placeholder specialist is not authored). No production
registration of `probe`.

## Acceptance (concrete, asserted values)
1. Registry validates `probe`; its exact manifest dict + gate map + field order asserted; `default_ceiling
   == "needs_alpha"`; `coding_registry().type_ids() == ("coding",)` (probe is test-only).
2. A `probe` ticket drives `needs_kickoff → needs_alpha → needs_beta → done` via real propose/accept, with
   the exact state / ceiling / parked-field / accepted-value / cleared-proposal / event-order asserted at
   each transition, and **no worker session created**.
3. `probe`'s `alpha`/`beta` values are stored and read back through the generic codec (t_tt02b).
4. Invalid probe state/field/ceiling + unknown-type create raise the exact `PlannerError` codes.
5. Coding behavior unchanged; `./verify` green.

## References
- Registry + fixture mechanism: `src/planner/ticket_types/`, `coding_bridge.set_registry_for_test`
  (t_tt02), the generic codec + threaded engine (t_tt02b).
- The go/no-go gate spec: `orchestration/ticket-types-redesign/PLAN.md` "First go/no-go — the falsifiable
  gate" (t_tt02x proves the engine/data half; t_tt03 proves the CLI/API/external-work half).
- Standing rules: `PRINCIPLES.md` (asserted-values acceptance; earn existence), `decisions.md` D102/D103.
