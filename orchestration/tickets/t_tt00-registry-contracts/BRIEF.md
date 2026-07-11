# Ticket t_tt00 — Ticket-type registry: interface, `coding` definition, served manifest

Phase 0 of `orchestration/ticket-types-redesign/PLAN.md`. **Contracts-only, zero behavior change.** This
ticket builds the registry *module* and registers `coding` as the first workflow definition, reproducing
the now-landed lifecycle exactly. Nothing in production consumes the registry yet — this is the deep
module the later phases route through.

## Goal (approved success)

A code registry that is the single authority for ticket-type workflow definitions. It:
- holds one `coding` definition that reproduces today's lifecycle **exactly** (verified against the live
  constants, not transcribed by hand into a test);
- **validates and refuses to boot on violation** (id uniqueness, complete gate coverage, one successor
  per non-terminal stage, reserved-word placement, field-reference integrity, worker-profile reference);
- serves **one serialized manifest** (the shape the CLI and web will later consume);
- exposes the derived lookups callers will ask for — gating field of `(type, state)`, advance target,
  field order, ceiling range + default ceiling, terminal status, serialized manifest — without exposing
  its internal tables.

No production caller is rewired in this ticket. The existing suite stays green **unchanged**.

## Scope boundary

**In:** the new registry module; the `coding` definition; the manifest serializer + its asserted shape;
the startup validator; the parity golden test; negative validation tests.

**Out (later phases, do not touch):** parameterizing `machine.py`/`resolution.py`/codec (t_tt01); the DB
`ticket_type` column + migration (t_tt02); the `probe` fixture type (t_tt02x); any CLI/API/manifest
*consumption* (t_tt03+); workers (t_tt05). Do not modify `tickets/contracts.py` enums, `machine.py`,
`db.py`, or any consumer. Do not delete or relax the existing `STATE_ORDER`/`WORKER_STATE_ORDER`/
`GATING_FIELD`/`ADVANCE_TARGET` constants — the golden test asserts the `coding` definition **equals**
them.

## Module location (recommended; planner + Codex to confirm)

`src/planner/ticket_types/` — a first-class sibling domain (like `tickets/`, `sprints/`). Rationale: the
registry is consumed cross-domain (tickets, CLI, web manifest, external-work), and the plan wants it in
its own module folder to minimize collisions with `tickets/contracts.py`. Import direction: consumers
import `ticket_types`; `ticket_types` may import the **leaf** `tickets/contracts.py` enums (which import
only `core/contracts`) to source `coding`'s vocabulary — no cycle, because `tickets/contracts.py` imports
nothing back. If the planner finds a cleaner seam, it may propose one with the no-cycle property intact.

## The `coding` definition it must reproduce (exact — from the landed code)

- **Stage order** (`= STATE_ORDER`, contracts.py:31): `needs_kickoff → needs_success → needs_approach →
  needs_plan → needs_implementation → needs_closeout → done`. First = `needs_kickoff` (reserved), last =
  `done` (reserved). `dropped` is the reserved exceptional terminal, reachable anywhere, outside the
  linear order.
- **Gate map** (`= GATING_FIELD`, contracts.py:73): each non-terminal stage gates its same-named field —
  `needs_kickoff→kickoff, needs_success→success, needs_approach→approach, needs_plan→plan,
  needs_implementation→implementation, needs_closeout→closeout`.
- **Advance map** (`= ADVANCE_TARGET`, contracts.py:84): each non-terminal advances one step;
  `needs_closeout → done`.
- **Field order** (`= FieldName` / `TicketFields`, contracts.py:43/108): `kickoff, success, approach,
  plan, implementation, closeout`. `kickoff` is the reserved leading field.
- **Ceiling range** (`= WORKER_STATE_ORDER`, contracts.py:37): stage order **minus** the leading
  `needs_kickoff` bookend — `needs_success … done`. These are the only legal ceilings.
- **Default ceiling**: `needs_success` (first entry of the ceiling range).
- **Worker profile** (declared, inert until t_tt05): a stanza naming a specialist skill, a model (+
  effort), and a toolset profile id. For `coding`, mirror today's worker (specialist = the existing
  `panels-worker` craft, model/toolset = current). Validated for reference integrity only; **nothing
  consumes it in this ticket.**
- **Transition-effect hook** (declared, inert until wired in t_tt01): coding's plan-handoff —
  `old=needs_plan, new=needs_implementation, implementer=khushal ⇒ user_takeover` (machine.py:141). In
  this ticket it is only *declared* on the definition; `plan_handoff_status` is not changed.

## Acceptance (concrete, asserted values — no "passes/works")

1. **Startup validation passes** for the registry containing only `coding`; and a **negative test** for
   each violation class rejects with a specific error: duplicate id, missing gate for a non-terminal
   stage, a non-terminal stage with no successor, first stage ≠ `needs_kickoff`, last stage ≠ `done`,
   `dropped` used as a linear stage, a gate pointing at an undeclared field, a worker profile pointing at
   an undeclared skill/toolset.
2. **Parity golden test** asserts the `coding` definition's derived views **equal the live constants**:
   stage order `== STATE_ORDER`; gate map `== GATING_FIELD`; advance map `== ADVANCE_TARGET`; field order
   `== tuple(FieldName)`; ceiling range `== WORKER_STATE_ORDER`; default ceiling `== needs_success`.
   Assert against the imported constants so any future drift in either fails this test.
3. **Manifest shape** asserted exactly: serializing `coding` yields a JSON object with — at least —
   type id + labels, ordered stages (each: id, label, gating-field id, is-terminal), advance map, ordered
   field set (id + label), ceiling range, default ceiling, and the worker-profile id. Assert the exact
   dict/JSON, not "has keys".
4. **No behavior change:** the full existing `./verify` suite passes unchanged; a structural assertion
   that no production module imports the registry yet (grep-style or import-graph test), i.e. this ticket
   is additive only.

## Decisions on the planner's open questions (orchestrator-confirmed)

All four are **inert in this ticket** (nothing consumes the worker profile, toolset profile, or labels
until t_tt04/t_tt05), so these freeze the asserted manifest values without any behavior effect; later
phases refine them when they become live.

1. **Coding worker profile** — confirm the planner's faithful-to-today values: `specialist_skill =
   "panels-worker"`, `model = None`, `reasoning_effort = None`, `toolset_profile = "default"`. This
   mirrors today (session.create passes no model/effort; toolsets are home-default). t_tt05 splits the
   base worker skill from the coding specialist and makes model/toolset real.
2. **`KNOWN_TOOLSET_PROFILES = {"default"}`** for reference-integrity validation — confirmed; real named
   profiles are a t_tt05 construct.
3. **Stage labels** `Kickoff / Success / Approach / Plan / Implementation / Closeout / Done` — confirmed
   and asserted exactly (the manifest becomes the single label source). Implementer: if `web/src/lib/ui.ts`
   already carries canonical label strings, reuse those exact strings to minimize the eventual t_tt04b UI
   diff; otherwise these stand.
4. **ErrorCode** — reuse the existing generic `ErrorCode.validation` with a specific `detail` naming the
   violation; do **not** add a new core enum member (additive-zero). A dedicated code can be added later
   if the startup audit needs to distinguish.

## References
- Plan: `orchestration/ticket-types-redesign/PLAN.md` — Phase 0, "The registry — the new deep module",
  invariants 1 & 9, "Registry validation — the enforcement doors".
- Design: `orchestration/ticket-types-redesign/RECOMMENDATION.md` §3 (registry), §8 (worker model).
- Landed lifecycle: `src/planner/tickets/contracts.py`, `src/planner/tickets/logic/machine.py`.
- Standing rules: `PRINCIPLES.md` (core/module contract; asserted-values acceptance), `CLAUDE.md`.
