# Ticket t_tt04a — Backend read-models: type-driven, non-throwing projections

Phase 4a — the first ticket of Phase 4. Make every **backend read-model** that decodes a ticket's
state/fields resolve the ticket's **own type** instead of assuming `coding`, so a non-coding ticket
projects correctly and never throws. Coding output stays **byte-identical**; production stays coding-only;
the non-coding type exercised in tests is the existing **`probe`** fixture (t_tt02x). This ticket does
**not** touch the frontend and does **not** reshape the board payload — the board *reshape* (dropping the
state-column taxonomy, consuming the manifest) lands with the web work in **t_tt04b**.

Depends on: t_tt00–t_tt03 (registry, generic engine + storage, manifest endpoint, `coding_bridge`), t_tt02x
(`probe`). Contract-scoped: implement against the registry/definition seam (`coding_bridge`,
`ticket_types/views.py` — `field_ids`, `gated_state`, `state_index`, stage labels) and the read-model
functions named below. Do not modify contracts, the engine, or the storage layer.

## The seams (each: resolve the ticket's `ticket_type` → validate/decode against its definition)

1. **`tickets/views.py::copy_text` (169)** — hardcodes the six coding slots
   (`kickoff/success/approach/plan/implementation/closeout`). Drive the emitted field blocks from the
   **type's declared `field_ids`** in order (each block = `<field>:` / `<field>_user_note:`), keeping the
   surrounding fixed lines (title/state/priority/implementer/recap/blocked_by/blocks) unchanged. Coding's
   output must be **character-identical** to today.
2. **`tickets/views.py::board_view` (234)** — three coding assumptions: `by_state = {s.value for s in
   STATE_ORDER}` + `by_state[state].append` **throws on any non-coding state**;
   `fields_from_json(..., coding_bridge.coding_definition())` decodes every row with coding's fields;
   `has_pending_gating_proposal(TicketState(state), fields)` coerces to coding. Fix: decode each row with
   **its own type's** definition; compute the pending-proposal signal via that definition; **do not group
   by a global state list** (no throw on unknown state). **Enrich each card** with: `ticket_type`,
   `state` (id) + `state_label`, `gating_field` (id) + `gating_field_label`, `is_done`, `is_dropped`
   (control signals the card already carries — `ticket_status`, `has_pending_proposal` — stay). Keep the
   emitted payload **frontend-compatible** (the unchanged web app + e2e must stay green): a coding-only
   board is byte-identical to today. The heterogeneous-state grouping question is 4b's, not this ticket's.
3. **`tickets/views.py::queues_view` / `_approval_digest` (303)** — `GATING_FIELD.get(TicketState(state))`
   coerces to coding. Resolve the row's type and take its **gating field for the current state** from the
   definition; a non-coding proposal must surface in the approval queue on the registry-selected field.
4. **`sprints/views.py` (116, 124)** — `fields_from_json(..., coding_bridge.coding_definition())` and
   `TicketState(state)`. Decode with the row's own definition; drop the coding coercion.
5. **`sprints/logic/status.py::derive_sprint_item_status` (23)** — `_IN_PROGRESS_STATES` is a **hardcoded
   coding mid-state set** (`needs_approach…needs_closeout`), i.e. product meaning: "in progress" = advanced
   **past the opening stages**, not merely non-terminal (it excludes `needs_kickoff` and `needs_success`).
   This is a **genuine per-type semantic, not a mechanical swap** — the plan must define what "in progress"
   means for an arbitrary type (e.g. state index beyond the type's opening stage(s) / default ceiling,
   excluding terminal), reproduce coding's current buckets **exactly**, and classify a `probe` ticket
   correctly. Do not silently narrow this to "non-terminal"; state the chosen rule and pin coding's map.

## Scope

**In:** the five read-model seams above, driven by the type registry/definition via `coding_bridge` +
`ticket_types/views.py`. A **permanent golden** pinning coding's `copy_text` field order and
`derive_sprint_item_status` buckets so the derivation can't drift. Tests against **both** `coding`
(byte-identical) and `probe` (correct, non-throwing).

**Out:** any `web/` change; the board payload **reshape** and manifest consumption (t_tt04b); scope/approval
picker UI (t_tt04b); worker realization (t_tt05); registering `new_worker` or any second production type
(the throwaway type is introduced with 4b/5). Contracts, engine, storage untouched.

## Acceptance (concrete, asserted values)
- **`copy_text`**: a `coding` ticket's rendered text is character-identical to today (golden); a `probe`
  ticket renders its **own** fields (`kickoff/alpha/beta` blocks + user-notes), no coding fields present.
- **`board_view`**: a mixed `coding`+`probe` board **does not throw** and emits each card with
  `ticket_type`, `state`+`state_label`, `gating_field`+`gating_field_label`, `is_done`, `is_dropped`,
  plus the existing control signals; a **coding-only** board payload is byte-identical to today (existing
  e2e board assertions unchanged).
- **`queues_view`**: a `probe` proposal surfaces in the approval digest on the registry-selected gating
  field; a `coding` queue is byte-identical.
- **`sprints`**: a sprint item with a `probe` child in a mid-stage rolls up to the correct `ItemStatus`;
  coding rollups are byte-identical (golden buckets asserted).
- `./verify` green; production registry coding-only.

## References
- Read-models: `src/planner/tickets/views.py` (`copy_text` 169, `board_view` 234, `queues_view`/
  `_approval_digest` 303), `src/planner/sprints/views.py` (116, 124), `src/planner/sprints/logic/status.py`.
- Registry/definition: `src/planner/ticket_types/views.py` (`field_ids`, `gated_state`, `state_index`,
  stage labels), `coding_bridge`. Fixture: `tests/support/probe.py` (t_tt02x).
- Plan: `orchestration/ticket-types-redesign/PLAN.md` Phase 4a. Standing rules: `PRINCIPLES.md`,
  `decisions.md` D102/D103.
