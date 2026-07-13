# Ticket t_tt03 — Type-driven CLI/API ingress + external-work genericization + the go/no-go gate

Phase 3 — the culminating ticket. Make the CLI/API ingress type-aware (create requires a type; propose/
scope/state/filters validate against the ticket's own type via a served manifest), finish the external-
work genericization deferred from t_tt02b, and prove the whole stack with the **falsifiable go/no-go
gate**: create and drive a `probe` ticket through **real** propose/accept to `done` via the CLI/API — no
worker. This is the milestone the owner chose to stop and report at.

Depends on: t_tt00–t_tt02b (registry, generic engine + storage), **t_tt02x** (the canonical `probe`
fixture). Production still ships **coding-only**; `probe` is test-only.

> The planner may recommend splitting this into t_tt03a (manifest endpoint + backend ingress + external-
> work genericization) and t_tt03b (CLI + the go/no-go gate) given its size. Decide at plan time.

## Scope — the ingress surface (make it type-driven)

**In:**
- **Manifest endpoint:** serve the registry's serialized manifest (one shape, all registered types) at a
  stable route the CLI and web consume — the single source for stage order / labels / gates / fields /
  ceiling range per type. (Web consumption is t_tt04b; this ticket just SERVES it and the CLI consumes it.)
- **Create requires a type (no default):** `ticket create --type <id>` mandatory at the CLI AND the API
  (`_marshal_create_ticket`/`create_ticket`, api.py:167/321) — reject a create with no/unknown type,
  surfacing the valid type list; remove the `ticket_type="coding"` data-layer default bridge (t_tt02) at
  the ingress so the type is explicit. `create_ticket` writes the type; ceiling = the type's default.
- **Type-driven per-position parsing** — resolve the ticket's `ticket_type` FIRST, then validate against
  its definition instead of global enums: the propose field (`POST /tickets/{id}/propose/{field}` — a
  field id validated against the type's `field_ids`, not global `FieldName`), scope `--ceiling`
  (validated against the type's `ceiling_range`), and any `/state` op (validated against the type's
  stages). `parse_enum(TicketState/FieldName, ...)` at ingress (api.py:138/301/356/396/417) becomes
  per-type validation via the manifest/registry.
- **External-work genericization (deferred from t_tt02b — finish it here):**
  - `_external_provided_values` (api.py:267-280) currently hardcodes the six coding fields → build the
    provided-values map from the **type's declared fields** (the external body's field keys per type).
  - `create_ticket_from_external_work`/`reconcile_ticket_from_external_work` (api.py:350/390) parse
    `target_state` as global `TicketState` → resolve the type, validate the target state against the
    type's stages; replace `create_ticket_from_external_work`'s **hardcoded `needs_success`** initial
    state (data.py) with the type's first worker stage / the provided state.
  - Replace `external_work._PREFIX_COUNT`/`_FIELD_ORDER` with a **gate-based** derivation — the settled
    prefix comes from the **non-terminal stages' `gating_field` order** (NOT `field_ids`, which the
    registry does not order-align — the t_tt02b review's field-order bug). Honor
    `supports_prefix_reconciliation` (a type may decline it). **Lift** t_tt02b's coding-only rejection.
  - Add a **permanent golden test** pinning coding's prefix map (needs_success:1 … done:6) so the
    derivation can't drift.
  - Thread the per-row definition through the external-work path (the last coding-default paths from D103).
- **The `GET /tickets?state=` filter** (api.py:413/417, `views.py`): reading `state` without a type is
  ambiguous across types — require `ticket_type` when filtering by a **non-reserved** state, or make it an
  explicit cross-type union (decide + document). Reserved bookends (`done`/`dropped`/`needs_kickoff`)
  filter across types fine.
- **CLI surfaces** (`cli/main.py`): the Chief external-work static Click choices / file options and the
  approval constants that enumerate coding fields → driven from the manifest/type. The **seed importer**
  already writes `coding` (t_tt02) — confirm it's type-correct.

**Out:** web manifest consumption + mixed-type board/scope UI (t_tt04b). Backend read models/copy_text/
queues genericization (t_tt04a). Worker realization (t_tt05). Registering `probe` in production (it stays
test-only).

## The go/no-go gate (the falsifiable proof — the run's milestone)
Reachable without a worker (`propose` needs no worker claim; accept is a human/API op). Do NOT use `/state`
jumps as the proof — drive the REAL gates via the CLI/API:
1. Create `ticket_type="probe"` via the API/CLI; assert exact initial state (`needs_kickoff`), default
   ceiling (`needs_alpha`), empty ordered fields (`kickoff/alpha/beta`), serialized type from the manifest.
2. At each non-terminal stage: file the current gating proposal with a non-empty recap via the API/CLI.
3. Assert it parks at the current ceiling on the exact field the registry selects.
4. Accept with an exact next ceiling + `at_cap`.
5. Assert accepted body, cleared proposal, next state, ceiling, and emitted event order.
6. Repeat through `done`.
7. Assert no worker turn / session was created.
8. Repeat one invalid field, state, ceiling, and a missing-type create; assert exact validation codes.
9. External-work **create + reconcile** for both `coding` and `probe`, including probe's prefix
   reconciliation (supports_prefix_reconciliation) and the coding golden prefix map.

## Acceptance (concrete, asserted values)
- Create rejects a missing/unknown type with the exact code + the type list; a coding create still works.
- The manifest endpoint returns the exact serialized shape for the registered types (asserted dict).
- Propose/scope/state validate per-type (asserted codes for invalid field/ceiling/state); a `probe`
  proposal parks on the registry-selected field; a `coding` ticket behaves byte-identically (existing
  suite unchanged for coding ingress).
- External-work create/reconcile works for `coding` (byte-identical) AND `probe`; the coding prefix-map
  golden holds; the gate-based derivation is asserted equal to coding's map.
- The **go/no-go gate** passes end to end (steps 1–9), no worker session created.
- `./verify` green; production registry coding-only.

## References
- Ingress: `src/planner/tickets/api.py` (marshal/parse/create/external-work/list), `src/planner/cli/main.py`,
  `src/planner/tickets/logic/external_work.py` (`_PREFIX_COUNT`/`_FIELD_ORDER`), `src/planner/tickets/views.py`.
- Registry/manifest: `src/planner/ticket_types/` (`manifest`, `field_ids`, `ceiling_range`, `gated_state`),
  `coding_bridge`. Canonical probe fixture from t_tt02x.
- Plan: `orchestration/ticket-types-redesign/PLAN.md` Phase 3 + "First go/no-go — the falsifiable gate".
  Standing rules: `PRINCIPLES.md`, `decisions.md` D102/D103.
