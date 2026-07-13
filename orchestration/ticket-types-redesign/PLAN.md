# Ticket types — implementation plan

Builds on `RECOMMENDATION.md` (the reviewed design, gpt-5.6-sol folded in) and the model settled with
the owner. This plans **how** to make `ticket_type` first-class as an *extension*, in phases that keep
`./verify` green throughout. The spine safety property: the existing `coding` lifecycle is routed
through the new machinery and proven identical **before** anything user-visible changes.

Altitude note: this is a phase/contract plan, not line-level code. Each phase becomes one (or a few)
contract-scoped tickets that run the normal per-ticket pipeline.

## Scope: build the machinery, not a second workflow

We build **all** the machinery, generic and N-type-ready, because a real second type is the very next
task. The boundary:

- **Machinery (this work):** the registry *system*, the parameterized engine, the DB `ticket_type`
  column + migration, the type-driven CLI/API + served manifest, the type-aware read models/board, and
  the full worker layer — profile resolver, the base-skill + specialist-skill *loading mechanism*, and
  per-toolset gateway routing. Everything type-agnostic and built to run N types.
- **Defining a workflow (the NEXT task — a separate responsibility, out of scope here):** the actual
  content of a real second type — its stages, gates, fields, and its specialist skill's craft. That is
  authoring a workflow, not building the system that runs workflows.

To *build and prove* generic machinery you need a second shape to build against. That is a **minimal
synthetic fixture type** (a few made-up stages, different field names, **registered only in tests**, not
shipped) — its sole job is to prove the machine is genuinely N-ary and not `coding`-in-disguise. It is
not "defining a workflow." Production ships only `coding`; the first real workflow plugs into the
finished machinery next.

---

## Settled invariants (the ground rules)

1. **Universal bookends.** Every type's lifecycle is `needs_kickoff → (type-specific middle stages) →
   done`; `dropped` is reachable anywhere. `needs_kickoff`, `done`, `dropped` are **reserved words**
   shared identically by all types. `needs_kickoff` **has landed** (commit `304734e`) as a real *gated*
   first stage: it carries a reserved gating field `kickoff` and advances `needs_kickoff → needs_success`
   through the ordinary propose/accept machinery. So the universal prefix is the **pair**
   `(state=needs_kickoff, gating field=kickoff)`, not a bare state — every type shares it identically.
2. **`ticket_type` is mandatory at creation.** No default. Existing rows are backfilled to `coding` by
   migration (fixing old data, not a creation default).
3. **States are per-type strings.** `(ticket_type, state)` is the real key — `state` is never read
   alone. The registry is the single validator.
4. **One backend registry authority + one served manifest.** No parallel TypeScript/CLI encoding of
   states.
5. **Strictly linear workflows.** One gating field per non-terminal stage, one successor per stage.
6. **`propose` stays position-relative for field routing but retains its recap** (atomic
   proposal + recap write; `--recap` unchanged).
7. **Workers: base skill + linked specialists.** One base worker skill + per-type specialist skills;
   differentiate inside a shared gateway child; fork children only per *toolset profile*.
8. **`ticket_type` is immutable after creation in v1.**
9. **Two orderings per workflow (landed with kickoff).** The kickoff change split the linear order in
   two: the **full stage order** `STATE_ORDER` (kickoff → … → done — drives `state_index`, advance,
   gating) and a **worker/ceiling range** `WORKER_STATE_ORDER` = the full order **minus the leading
   `needs_kickoff` bookend` (first worker stage → done — the only values a ceiling may take;
   `validate_ceiling`/`resolve_scope` key off *this*, never `STATE_ORDER`). The **default ceiling** is
   the first worker stage (`needs_success` for coding). Both orderings, and the default, are now
   **per-type** and must be derived from the registry — not the two module constants they are today.

---

## The registry — the new deep module

A `ticket_type` definition declares:
- stable id + display labels
- ordered stages — first is the shared prefix `needs_kickoff` (gated by the reserved `kickoff` field),
  last is `done`
- the gating field for each non-terminal stage
- the ordered field set (always led by the reserved `kickoff` field)
- a worker profile: specialist skill, model (+ effort), toolset profile
- optional transition-effect hooks (e.g. coding's `plan → implementation` + human override ⇒
  `user_takeover`)

From the ordered stages the registry **derives** (callers never hand-build these): the full stage order
(for indexing/advance/gating), the **ceiling range** = stage order minus the leading `needs_kickoff`
bookend (the only legal ceilings), and the **default ceiling** = first entry of that range. These replace
the module-global `STATE_ORDER` / `WORKER_STATE_ORDER` and the hard-coded `needs_success` default.

At startup it **validates and refuses to boot on violation**: id uniqueness, complete gate coverage,
one successor per non-terminal stage, terminal placement (`needs_kickoff` first / `done` last /
`dropped` reserved), field references, worker-profile references. It serves **one serialized manifest**
consumed by both CLI and web. Changing a registered workflow (renaming/removing a stage or field) is a
data migration and must fail startup validation against stored rows — never silently reinterpret.

Callers ask it for: gating field of `(type, state)`, advance target, field order, the ceiling range +
default ceiling, terminal status, serialized manifest — without seeing its internal tables. Core holds
zero per-type conditionals (the standing core/module rule).

---

## Hard precondition (P0) — SATISFIED

**`needs_kickoff` has landed** (commits `f80c0e7` → `e411dd1` → merge `304734e`, "make kickoff a normal
ticket stage"). The precondition this plan was blocked on is cleared; `t_tt00` is unblocked. What landed,
and what it changes for this plan:

- `needs_kickoff` is a full **gated** first stage: `FieldName.kickoff`, entries in
  `GATING_FIELD`/`ADVANCE_TARGET`/`FIELD_GATES`, a `kickoff` `FieldSlot`, and a `kickoff` slot in the DB
  `fields` JSON default. It advances `needs_kickoff → needs_success` through ordinary machinery — the
  universal prefix is now a `(state, field)` pair (invariant 1).
- **New second ordering `WORKER_STATE_ORDER`** (contracts.py:37) = the linear order minus
  `needs_kickoff`; `validate_ceiling`/`resolve_scope` (machine.py:95/102) key off it; the DB `ceiling`
  CHECK now excludes `needs_kickoff` (db.py:71). This is the ceiling-range concept the registry must
  model per-type (invariant 9).
- Kickoff dragged in **new coding-specific literals** the parameterization must absorb: the
  recap-writability guard names `needs_kickoff` + `needs_success` (admission.py:74); a direct-jump guard
  names `needs_kickoff` (resolution.py:275); `SETTLED_PREFIX_INDEX` maps coding states → prefix indices
  (external_work.py:29); the default ceiling `needs_success` is hard-coded (data.py:303/391/397).
- A fresh migration `_migrate_ticket_kickoff_columns` (db.py:272) is now the **newest, closest template**
  for the Phase-2 `ticket_type` migration (atomic swap / FK-disable-check / SAVEPOINT rollback, plus a
  fields-JSON rebuild).

Before dispatching `t_tt00`: run one clean `./verify` on the current tree to confirm the landed lifecycle
is green as the baseline this plan extends.

## Phases

Each phase ends green on `./verify`. Phases 0–1 route `coding` through the registry and prove parity
before any user-visible change; 0–3 are the critical path for the owner's core question (states in the
DB + CLI operation). Full corrected sequencing, enforcement doors, and tightened acceptance are in the
**Review corrections** section below (gpt-5.6-sol plan review, folded in).

### Phase 0 — Registry + contracts skeleton (contracts first)
- Define the registry interface + manifest shape; register `coding` as the first definition,
  reproducing the **now-landed** lifecycle exactly: `needs_kickoff → needs_success → needs_approach →
  needs_plan → needs_implementation → needs_closeout → done`, with `kickoff` as the leading field.
- Nothing else consumes the registry yet; no behavior change.
- **Acceptance:** registry startup validation passes; a golden test asserts the `coding` definition
  equals the current stage order / gating map / advance map / field set **and** the derived ceiling
  range (`STATE_ORDER` minus `needs_kickoff` = today's `WORKER_STATE_ORDER`) and default ceiling
  (`needs_success`) — asserted against the live constants so a drift in either fails.

### Phase 1 — Parameterize machine + resolution + codec by the type (with a pre-persistence bridge)
- The generic functions take a **workflow definition explicitly**; every production caller temporarily
  passes `registry.require("coding")`. The codec becomes `fields_from_json(raw, coding_definition)`,
  not a lookup from a ticket row (which has no `ticket_type` until Phase 2). This is the bridge that
  lets Phase 1 be green *before* persistence; Phase 2 removes it by resolving the definition from each
  row's stored type. (If Phase 1 instead resolved from `ticket.ticket_type`, Phase 2 would have to
  precede it — the bridge is what keeps the order safe.)
- Thread the workflow through the machine in place of module constants — **both orderings**: the full
  stage order (for `state_index`/advance/gating) *and* the ceiling range (for `validate_ceiling`/
  `resolve_scope`, machine.py:95/102, which key off `WORKER_STATE_ORDER` today). Generalize terminal
  detection to the reserved bookends; scope resolution, admission, and the accept/edit/drop/jump rules
  read the definition. Codec becomes a validated mapping over the type's declared fields (drop the
  unsafe catch-all default).
- **New coding-specific literals kickoff added, to parameterize here:** the recap-writability guard
  (admission.py:74) names `needs_kickoff` + `needs_success` — generalize to "past the first worker
  stage"; the direct-jump guard (resolution.py:275) names the reserved `needs_kickoff` (keep the
  reserved-bookend reference, just de-identity it); `SETTLED_PREFIX_INDEX` (external_work.py:29) is a
  coding-state→index map — derive it from the type's ordered stages (this is a Phase-3 external-work
  concern, flagged here so it isn't missed).
- **Replace every workflow-ID `is`/identity comparison with value equality.** The set grew with kickoff
  — beyond `machine.py:83`, at minimum: `resolution.py:153/165/202/271/273/275/280/288/292`,
  `external_work.py:54/113`, `admission.py:61`, and `machine.py:147` (`plan_handoff_status`). Reserved
  bookends (`done`/`dropped`/`needs_kickoff`) may still be named literally, but the comparison must be
  `==`. Per-type states/fields are strings — identity breaks. Acceptance must use
  dynamically-constructed-but-equal strings so Python interning can't mask a surviving `is`.
- **Acceptance:** the full existing ticket-engine / resolution / admission / readiness suite passes
  unchanged with `coding` routed through the registry (parity proof); the machine imports neither
  `STATE_ORDER` nor `WORKER_STATE_ORDER` (nor the gate/advance maps); an assertion that no production
  caller reads lifecycle from the old constants and every temporary resolution is exactly `coding`.

### Phase 2 — Database: type column, integrity moves to the registry
- Add `ticket_type` (required at the write layer). Drop **both** enumerating `CHECK`s — on `state`
  (db.py:62) and on `ceiling` (db.py:71, which now excludes `needs_kickoff`); keep the type-independent
  checks (non-empty, `at_cap`, `ticket_status`, priority). Add registry validation on row load and
  before persist, plus a **startup integrity audit** over the tickets table.
- Derive the **default ceiling per-type** (first worker stage) instead of the hard-coded `needs_success`
  at write/create (data.py:303/391/397) and the DB `ceiling` default — coding's stays `needs_success`,
  but the source becomes the registry.
- Migration: mirror the **just-landed** `_migrate_ticket_kickoff_columns` (db.py:272) — atomic swap /
  FK-disable-check / SAVEPOINT rollback, and it already rebuilds the `fields` JSON (now including the
  `kickoff` slot), so it's the closest template. Backfill every row to `coding`; ensure no later
  migration template recreates the old CHECKs or the fixed-field default.
- **Acceptance (tightened, from review):** all pre-migration row IDs/counts survive; every migrated
  `ticket_type == "coding"`; state/ceiling/fields/status/session-key/relationships/timestamps
  unchanged; `PRAGMA foreign_key_check` returns `[]`; final `sqlite_master.sql` has
  `ticket_type TEXT NOT NULL`, no creation default, and no enumerating state/ceiling CHECK; re-running
  `create_schema` is idempotent; a failure during copy/swap preserves the original table; each
  corruption case fails startup with the ticket id + reason (unknown type, invalid state/ceiling,
  missing/extra field, malformed slot); later legacy rebuild templates cannot recreate the retired
  CHECK/default; index strategy decided explicitly (likely `(ticket_type, state)`).

### Phase 2.5 — Minimal fixture type for the machinery proof (`t_tt02x`, contract-only; depends on `t_tt00`)
- A **synthetic** second type (`probe`) used **only in tests** to exercise the machinery with a shape
  structurally different from `coding` — deliberately minimal, not a designed workflow: a short
  made-up stage order (`needs_kickoff → needs_alpha → needs_beta → done`) sharing the reserved
  `needs_kickoff`/`kickoff` prefix, a gate-to-field map, ordered fields (`kickoff`, `alpha`, `beta`),
  labels, a `prefix-reconciliation: yes` flag, and a worker-profile id pointing at a placeholder.
  Because its ceiling range is `needs_alpha → done` and its default ceiling is **`needs_alpha`** (not
  `needs_success`), it also proves the ceiling-range + default-ceiling derivation is genuinely per-type.
  Registered by the **test harness**, not the production registry — so the live app still offers only
  `coding`.
- Its only purpose: force out `coding`-specific assumptions (the `is`→`==` string bug, hidden literal
  field names, closed-world spots) that `coding` alone can't reveal. Not "defining a workflow."
- **Acceptance:** the registry validates `probe`; its manifest JSON is asserted exactly; golden tests
  for its gate map and field order; production registry contains only `coding`.

### Phase 3 — CLI + API + Chief external-work + seed importer (the owner's core question)
- `ticket create --type` mandatory; reject a create without a type and surface the type list.
  create / propose / approve operate relative to the current position via the served manifest;
  `propose` keeps `--recap`; scope `--ceiling` validated against the type's stages by the server.
- **Own the whole ingress surface** (review found it wider than first named): the external-work wire
  contracts and allowed-key parsing (`contracts.py:155` — now carries `kickoff_note` + the ordered
  field keys; `api.py` marshal), the global-`FieldName` parse, scope/state routes that parse global
  `TicketState` before loading the ticket, the `GET /tickets?state=` filter that reads state without a
  type (`views.py:84` — require `ticket_type` when filtering by a non-reserved state, or make it an
  explicit cross-type union), the CLI approval constants and Chief CLI static Click choices/file
  options, and the **seed importer** (direct insert of fixed coding fields, no discriminator —
  `seed/importer.py`). (Re-resolve exact line refs at ticket time; kickoff's merge shifted them.)
- Chief external-work: select `ticket_type` before parsing state/fields; **replace the hard-coded
  `SETTLED_PREFIX_INDEX` coding-state→index map (external_work.py:29)** with one derived from the type's
  ordered gates; a type may declare it doesn't support prefix reconciliation.
- **Acceptance:** the falsifiable go/no-go gate at the bottom of this plan (create + drive the fixture
  `probe` type through *real* propose/accept gates to `done`, no worker), plus external-work
  create/reconcile for `coding` + `probe`.

### Phase 4 — Read models: all backend projections + board + scope UI (split 4a backend / 4b web)
- **4a (backend read contracts):** every projection that decodes state/fields — ticket detail and
  `copy_text` (five fixed fields, `views.py:143`), approval queues (global `GATING_FIELD`,
  `views.py:260`), sprint summaries that decode state without type, and the board. Board cards carry
  `ticket_type`, current state id + label, gating field + label, is_done/is_dropped, control signals;
  keep **project** and **`ticket_status`** as the common axes; remove the global state-column taxonomy
  (`by_state[state]` must not throw on an unknown state).
- **4b (web):** the web app consumes the served manifest (no parallel TS lifecycle — `ui.ts:3` is
  deleted as canonical); scope pickers read the type's stage order (one source); header floor = current
  state onward; approval floor = newly-entered state onward + `none`.
- **Event mapping (review):** `test_frontend_event_mapping_covers_backend_event_kinds` stays green; add
  exact assertions that `ticket_created`/`state_changed`/`scope_changed`/`proposal_accepted` invalidate
  `ticket:<id>`, `board`, `queues`, `sprint:current`; the manifest resource adds no client-side
  lifecycle copy. (No new event kind is needed — events invalidate by entity prefix.)
- **Acceptance:** a mixed-type board renders without error; scope pickers offer the exact per-type
  stages (asserted, not "correct"); copy_text/queues/sprint summaries render a non-coding type; e2e
  green.

### Phase 5 — Worker realization: the mechanism (base skill + specialist loading + profile routing)
- Build the worker **mechanism**, N-type-ready: a worker-profile resolver mapping
  type → `{specialist skill, model, toolset profile}`; gateway children keyed by *toolset profile*
  (shared where equal); the employee runner and human chat both routing through the resolver; model +
  effort set per session; toolset per child via `HERMES_TUI_TOOLSETS`; the runner prompt carrying the
  ticket type + specialist-skill selection.
- Author the **base worker skill** in full (shared orientation, propose-only, CLI, gates, artifacts)
  and the specialist-skill **loading** mechanism (base links to a type's specialist). Prove
  loading + routing with a **placeholder specialist** for the fixture `probe` profile — authoring a
  *real* specialist skill is part of the next task (defining a real workflow), not this build.
- **Acceptance (both routing entry points, from review):** the automatic employee turn *and* human
  ticket chat both resolve `ticket_type → worker profile → toolset child`; new sessions receive the
  exact model/effort payload (`session.create` currently passes neither — `shared_gateway.py:683`);
  existing sessions resume through the same child; the runner prompt contains the ticket type +
  specialist-skill selection (it currently contains neither — `employee_step_runner.py:37`); two types
  sharing a profile reuse one child; two profiles use different children; restricted tools fail at the
  gateway/tool layer, not via skill text; shutdown de-dupes shared children.

---

## Ticket decomposition (corrected order, from review)

Each ticket runs the full pipeline (plan → Codex plan review → implement → Codex diff review → serial
integrate → `./verify`). They **integrate serially** — they overlap `contracts.py`/`data.py`/
`machine.py`/`db.py`/`views.py` and each depends on shapes the previous created. Keep the registry in
its own module folder to minimize collisions with `tickets/contracts.py`.

| Order | Ticket | Main scope | Depends on | Status |
|---|---|---|---|---|
| Pre | kickoff landing | New coding lifecycle + migrations (owner work) | — | ✅ merge `304734e` |
| 0 | `t_tt00` | Registry contracts, `coding` definition, manifest schema | kickoff landed (✅) | ✅ `824aa58` |
| 1 | `t_tt01` | Parameterize engine via `coding_bridge` seam (Tier-1 string-native / Tier-2 coding-bound); value-equality | `t_tt00` | ✅ `eeef51d` |
| 2 | `t_tt02` | `ticket_type` persistence, migration, row/write validation, startup audit, per-type default ceiling, seed importer; coding-only, `TicketFields` stays fixed | `t_tt01` | in progress |
| 2b | `t_tt02b` | **Generic per-type field storage** (`TicketFields` fixed struct → per-field-id slot map; `get_slot`/`with_slot`/codec generic; lift `require_coding_field`) + **Tier-2 scope** (`ScopePair`/`resolve_scope`/`validate_ceiling` widen to the type's ceiling ids). Contract shape change — unblocks probe's fields + scope. | `t_tt02` |
| 2.5 | `t_tt02x` | Minimal fixture type `probe` (test-registered) + contract tests; needs generic storage | `t_tt00`, `t_tt02b` |
| 3 | `t_tt03` | Manifest endpoint, dynamic API/CLI parsing, create, external-work, filters | `t_tt02x` |
| 4a | `t_tt04a` | Backend ticket/board/queue/sprint/copy read contracts | `t_tt03` |
| 4b | `t_tt04b` | Web manifest consumer + mixed-type UI | `t_tt04a` |
| 5a | `t_tt05a` | Base worker skill + specialist-loading mechanism + `probe` placeholder | `t_tt02x` |
| 5b | `t_tt05b` | Gateway/session/runtime/human-chat profile routing | `t_tt03`, `t_tt05a` |

Only `t_tt05a` (skill authoring) is a realistic parallel lane — it can run alongside Phase 4 once the
exploration/profile contracts are frozen. All merges and `./verify` runs remain serial.

## Registry validation — the enforcement doors (from review)

"Validate on load and before persist" must name its doors, or a path slips through:
- `_row_to_ticket` — resolve type, decode fields against it, validate state/ceiling.
- `_apply_decision` — validate the complete prospective tuple before issuing SQL (the single canonical
  writer; keep a structural regression test that state/ceiling updates flow only through it).
- Every creation/import path (`create_ticket`, external-work create, **seed importer**) — build and
  validate a complete registered ticket before insert.
- **Note writes** — they rewrite the whole fields JSON *outside* `_apply_decision` (`data.py:827`);
  validate resulting field keys/slots before serialization.
- Startup audit — reuse the *same* validator (one linear scan, report the first corrupt ticket).

## Transition-effect (plan-handoff) semantics — decide explicitly (from review)
Today the coding handoff fires on accepted / auto-accepted proposals; direct state jumps and external
reconciliation bypass it. The registry hook contract must state when it runs. v1 parity-preserving
choice: **gating acceptance only** — and test auto-accept, direct accept, direct jump, and external
reconciliation explicitly.

---

## Out of scope for v1 (named, not silently dropped)
- **Authoring the first real second workflow** (e.g. `exploration`) — its stages/gates/fields and a
  real specialist skill. This is the explicit **next task**, a separate responsibility. This build
  ships production `coding`-only but leaves the machinery fully N-type-ready, so that next task is
  "register a definition + author a specialist," not "extend core."
- Runtime-editable / DB-defined types (code registry only).
- Branching or non-linear workflows.
- Changing a ticket's type after creation (immutable).
- Folding `implementer` into type. **Separate cleanup:** the muddled `Implementer` enum (a person + a
  generic worker + two model-flavoured identities, only `khushal` effective) is redesigned as a clean
  instance-level execution override on its own track — not blended into `ticket_type`.

## Carried risks (from the gpt-5.6-sol review)
- **Universal terminals.** Cross-domain SQL reads exact state strings (blockers, sprint-item
  completion, queues); safe only because kickoff/done/dropped are reserved and shared. A type with a
  custom terminal would block forever / stall its sprint item — prevented by registry validation (last
  stage must be `done`).
- **External-work is a first-class workflow interface**, not a codec tweak.
- **Board cannot consume a heterogeneous state union** — Phase 4 reshapes it; until then it must not
  throw on an unknown state.
- **Relaxing DB CHECKs is a real reduction in DB enforcement** — replaced by registry validation on
  load/write + startup audit, not left unguarded.
- **Migration ordering.** A later rebuild template must not recreate old constraints/defaults.

## First go/no-go — the falsifiable gate (end of Phase 3, from review)
Reachable without a worker (`propose` needs no worker claim; accept is a human/API op). **Do not use
`/state` jumps as the proof** — that bypasses gates and would only prove string storage, not the engine.
Drive through the *real* gates:
1. Create `ticket_type="probe"` (the fixture); assert exact initial state (`needs_kickoff`), default
   ceiling (`needs_alpha` — its first worker stage, *not* `needs_success`, which alone proves the
   per-type default-ceiling derivation), empty ordered fields (`kickoff`, `alpha`, `beta`), serialized
   type.
2. At every non-terminal stage: file the current gating proposal with a non-empty recap.
3. Assert it parks at the current ceiling on the exact field the registry selects.
4. Accept with an exact next ceiling + `at_cap`.
5. Assert accepted body, cleared proposal, next state, ceiling, and emitted event order.
6. Repeat through `done`.
7. Assert no worker turn / session was created.
8. Repeat one invalid field, state, ceiling, and a missing-type create; assert exact validation codes.
9. External-work create + reconcile for both `coding` and `probe`, incl. unsupported-prefix behavior
   if declared.

This proves persistence, registry selection, codec behavior, position-relative routing, scope
validation, single-writer transitions, and manifest consumption — the right point to stop before UI and
workers. If it holds, the rest is mechanical.

## Acceptance language (standing rule)
Replace every "passes / works / renders" with exact asserted values — state orders, maps, payloads,
error codes, migrated values, event order, route responses (PRINCIPLES.md:37).

---

_Design plan, not verified against a build. gpt-5.6-sol plan review (eng/architecture lens) folded in:
verdict `DONE_WITH_CONCERNS` → corrected. Companion: `RECOMMENDATION.md` (§7 design review, §8 worker
model)._
