# Ticket t_tt04b — Web: consume the manifest, render variable per-type stages, add the worker field

Phase 4b — the frontend half of Phase 4. Retire the hardcoded coding lifecycle in the web app and drive
stage rendering / the scope leash from the **served manifest** keyed by the ticket's own `ticket_type`, so
a non-coding type renders its own stages. Add **one new field** — the worker/type pill — to the ticket
facts line. Nothing else about the UI changes; coding renders byte-identically.

Depends on: **t_tt03** (`GET /api/ticket-types` manifest endpoint) and **t_tt04a** (board cards now carry
`ticket_type`/`state_label`/`gating_field`/`gating_field_label`/`is_done`/`is_dropped`). Production stays
coding-only.

> This is the ONLY frontend ticket of the type work. Keep it surgical: the app looks and behaves exactly
> as today for coding; the only visible addition is the worker pill.

## Scope

**In:**
- **Consume the manifest.** Fetch `GET /api/ticket-types` as a keyed resource (`manifest` / per-type
  lookup). Expose, per `ticket_type`: ordered stages (id + label), the gating field per non-terminal
  stage, the ordered field ids, terminal flags, and the ceiling range. One source of lifecycle truth.
- **Retire the hardcoded lifecycle in `web/src/lib/ui.ts`.** Today it hardcodes the coding lifecycle:
  `FIELD_NAMES`, `STATE_ORDER`, `GATING_FIELD`, `GATED_STATE`, `ADVANCE`, and the functions built on them
  (`gatingField`, `advanceTarget`, `ceilingOptions`, `fieldIsPassed`, `ticketStageVisualState`,
  `fieldStageVisualState`). These become **type-parameterized** — derived from the manifest for the given
  ticket's type — not module constants. `stateLabel`/`labelize`/`fieldSlot`/status/format helpers stay.
- **`TicketRoute.svelte`:** render the field sections by the type's ordered field ids (not the fixed
  `FIELD_NAMES`); the scope **leash** ceiling options come from the type's ceiling range (not
  `STATE_ORDER`); the recap-gate and per-field visual state come from the type's stages/gating.
- **`BoardRoute.svelte`:** the card stage rail derives from the type's gating — prefer the fields 4a now
  puts on each card (`gating_field`, `state_label`, `is_done`) over recomputing from a hardcoded map.
- **Add the worker field.** A pill in the ticket facts line (sibling of `implementer`) showing the
  ticket's `ticket_type` (label from the manifest). **Display-only** (owner decision 2) — the type is chosen
  at creation and a ticket's state/fields belong to its type, so it is not a post-hoc picker.
- **Event mapping stays additive.** The manifest resource adds no client-side lifecycle copy; events keep
  invalidating by entity prefix (no new mapping). `test_frontend_event_mapping_covers_backend_event_kinds`
  stays green.

**Out:** the mixed-type board *reshape* beyond what 4a already emits (columns stay project-grouped as
today); worker realization (t_tt05); registering any second type in production; making the worker pill an
editable type-switcher.

## Acceptance (concrete)
- The web app consumes `GET /api/ticket-types`; `ui.ts` no longer hardcodes `FIELD_NAMES`/`STATE_ORDER`/
  `GATING_FIELD`/`GATED_STATE`/`ADVANCE` as the canonical lifecycle (they are manifest-derived per type).
- A **coding** ticket and board render byte-identically to today: same field sections in order, same leash
  ceiling options, same stage-rail marks — existing e2e (`test_ticket_*`, `test_board_stage_indicators`)
  unchanged and green.
- A **non-coding** type renders its own stages / leash / worker pill — asserted by a frontend unit test
  feeding the render logic a synthetic two-type manifest (owner decision 1); production stays coding-only.
- The worker pill appears in the facts line with the type's label; `svelte-check` + `npm run build` clean;
  `./verify` green.

## Owner decisions (settled)
1. **Testing a non-coding type in the frontend = a unit test with a synthetic two-type manifest.**
   Production is coding-only, so the running app only ever serves the coding manifest. Prove the render
   logic adapts by feeding it a synthetic two-type manifest in a frontend unit test (`web/tests/`), NOT by
   shipping/registering a second type. The e2e still asserts coding renders correctly *from the manifest*.
2. **Worker pill is display-only (for now).** Show the ticket's type label; not an editable type-switcher
   (type is set at creation; a ticket's state/fields belong to its type). Editable switching is out of scope.

## References
- `web/src/lib/ui.ts` (the hardcoded lifecycle to retire), `web/src/routes/TicketRoute.svelte`,
  `web/src/routes/BoardRoute.svelte`, `web/src/lib/api.ts`/`resources`.
- Manifest: `GET /api/ticket-types` (t_tt03, `core/server.py`), `ticket_types/logic/manifest.py`.
- Plan: `orchestration/ticket-types-redesign/PLAN.md` Phase 4b. Owner UI ruling: worker = a facts-line
  field; stages just render variably (this session).
