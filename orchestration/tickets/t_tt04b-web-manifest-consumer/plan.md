# Implementation Plan — t_tt04b: Web manifest consumer (retire hardcoded coding lifecycle)

## 0. Objective and the one hard bar

Retire the hardcoded coding lifecycle in `web/src/lib/ui.ts` and drive stage rendering + the scope leash
from the **served manifest** (`GET /api/ticket-types`), keyed by each ticket's own `ticket_type`. Add
**one** display-only worker/type pill to the ticket facts line. Everything else is unchanged.

**The correctness bar is byte-identity for `coding`.** For `type_id === "coding"` the derived stage order,
labels, gating map, field ids, ceiling options, and stage visual states must equal today's hardcoded values
exactly. Proven two ways: (1) a frontend unit assertion that the coding manifest reproduces the old
`STATE_ORDER` / `GATING_FIELD` / `FIELD_NAMES` / `ADVANCE`; (2) the existing e2e suite (`test_flows_a`,
`test_flows_b`, `test_board_stage_indicators`) stays green unchanged, because it asserts leash options and
stage marks by **id/value**, not by any recomputation we're removing.

## 1. Ground truth (verified against the running serializer — do not re-litigate)

`GET /api/ticket-types` → `{ "types": ManifestDict[] }`, one entry per registered `type_id`. Per-type keys
(confirmed served): `type_id, label, stages, dropped, advance, fields, ceiling_range, default_ceiling,
worker_profile_id`. For `coding`:

```
stages:  [{id,label,gating_field,is_terminal}...]  full order needs_kickoff … done (NOT dropped)
dropped: {id:"dropped", label:"Dropped", gating_field:null, is_terminal:true}
advance: { needs_kickoff:"needs_success", …, needs_closeout:"done" }   non-terminal → next
fields:  [{id,label}...]  ordered, kickoff first → ids kickoff,success,approach,plan,implementation,closeout
ceiling_range: ["needs_success","needs_approach","needs_plan","needs_implementation","needs_closeout","done"]
default_ceiling: "needs_success"
worker_profile_id: "panels-worker"
```

**Byte-identity mapping** (each retired ui.ts constant, from a `ManifestDict m`):
- `FIELD_NAMES` ≡ `m.fields.map(f => f.id)`
- `STATE_ORDER` ≡ `m.stages.map(s => s.id)` (includes trailing `done`)
- `GATING_FIELD[state]` ≡ `Object.fromEntries(m.stages.filter(s => !s.is_terminal).map(s => [s.id, s.gating_field]))`
- `GATED_STATE[field]` ≡ inverse of that gating map
- `ADVANCE` ≡ `m.advance`
- leash ceiling range ≡ `m.ceiling_range`

**Critical label subtlety (must preserve):** today `ceilingOptions(floorState)` labels each option with
`stateLabel(state)` — underscores→spaces, **no capitalization** (`"needs success"`). The manifest's
`stage.label` is capitalized (`"Success"`). The leash option label must **stay** `stateLabel(state_id)`,
NOT the manifest label. No e2e asserts the visible leash text (they assert `.input_value()` = the state id),
but the mockup wording must not silently change. Rule: **stage-rail / field-section derivation uses ids from
the manifest; the leash option label continues to use `stateLabel(id)`.**

**`ticket_type` is present in both payloads.** `ticket_json` (used by `ticket_detail`) includes
`"ticket_type"`; board cards include `ticket_type`, `state_label`, `gating_field`, `gating_field_label`,
`is_done`, `is_dropped` (from 4a). Production serves coding only.

**Static-manifest / event-mapping proof.** The completeness test
`test_frontend_event_mapping_covers_backend_event_kinds` shells out to `web/tests/event-mapping.test.mjs`,
which only exercises `keysForEvent` in `eventMapping.mjs`. The manifest resource is fetched once and is
**never event-invalidated** — it introduces no event kind and no `eventMapping.mjs` entry. So the
completeness test is untouched by construction. (Manifest types are static per deploy.)

## 2. Blast radius — the real consumer set (wider than the brief's three files)

Grepping every reference to the retiring symbols and to `TicketStageSection` reveals **eight** files. This
is the biggest risk and the reason for the signature-change strategy below.

1. `web/src/lib/ui.ts` — defines them (retire constants; move functions to `lifecycle.ts`).
2. `web/src/routes/TicketRoute.svelte` — `FIELD_NAMES`, `STATE_ORDER`, `ceilingOptions`, `fieldStageVisualState`, `fieldSlot`. **Adds the worker pill.**
3. `web/src/routes/BoardRoute.svelte` — `gatingField(card.state)`, `ticketStageVisualState`.
4. `web/src/components/TicketStageSection.svelte` — internally calls `gatingField`, `fieldIsPassed`, `advanceTarget` (leaf; used by TicketRoute AND ReviewRoute).
5. `web/src/components/ScopePairPicker.svelte` — `ceilingOptions(newState || "needs_success")`.
6. `web/src/routes/ReviewRoute.svelte` — `FIELD_NAMES`, `fieldStageVisualState`, `gatingField(detail.state)`.
7. `web/src/components/ApprovalBlock.svelte` — only `labelize` (kept). No change beyond a pass-through prop.
8. `web/src/routes/SprintRoute.svelte` — only `labelize` (kept). No change.

Kept helpers (do NOT touch): `stateLabel`, `labelize`, `fieldSlot`, `markerLabel`, `ticketStatusLabel`,
`statusDisplay`, `formatUnix`, `errorCode`, `errorMessage`, `PRIORITIES`, `PRIORITY_ORDER`, the
`FieldStageVisualState` / `TicketStageVisualInput` types.

## 3. Architecture — how the per-type lifecycle reaches the functions

The retiring functions read module constants; they must instead read the manifest for a specific `type_id`.
Chosen shape (B): a small **`Lifecycle`** value object (a derived, memoized view of one type's manifest) as
the functions' first argument. Matches the backend `views.py` pattern, keeps the functions pure and
unit-testable with a synthetic manifest, gives callers one thing to pass. (Rejected: (A) raw `ManifestDict`
recomputed per call; (C) a global mutable "current type" — hidden canonical state, breaks the
single-source-of-truth rule and test isolation.)

### 3.1 New module `web/src/lib/lifecycle.ts` (pure, framework-free — mirrors `views.py`)

Types: `ManifestStage`, `ManifestField`, `TicketTypeManifest`, `TicketTypesResponse`.

`Lifecycle` — built once per manifest entry:
```
type Lifecycle = {
  typeId: string; typeLabel: string;      // typeLabel = m.label — the worker pill text
  fieldIds: string[];                      // ≡ FIELD_NAMES
  stateOrder: string[];                    // ≡ STATE_ORDER (includes done)
  gatingField: Record<string,string>;      // ≡ GATING_FIELD
  gatedState: Record<string,string>;       // ≡ GATED_STATE
  advance: Record<string,string>;          // ≡ ADVANCE
  ceilingRange: string[];                  // m.ceiling_range
  fieldLabel: Record<string,string>;
  stageLabel: Record<string,string>;
};
function buildLifecycle(m: TicketTypeManifest): Lifecycle   // pure
```
Retired functions re-homed here as `Lifecycle`-first-arg variants (mechanical lift of each body,
`STATE_ORDER`→`lc.stateOrder`, etc.). **Every one takes `lc: Lifecycle | null` and, when null, returns its
pre-load default so callers pass `lc` directly WITHOUT a guard at each site (Codex F1):**
```
gatingFieldFor(lc: Lifecycle | null, state): string | null            // null when lc null
advanceTargetFor(lc: Lifecycle | null, state, ceiling): string | null // null when lc null
ceilingOptionsFor(lc: Lifecycle | null, floorState): {value,label}[]  // [] when lc null; label = stateLabel(id) — PRESERVED
fieldIsPassedFor(lc: Lifecycle | null, field, state): boolean         // false when lc null
ticketStageVisualStateFor(lc: Lifecycle | null, { ticketState, ticketStatus, fieldName, fieldHasProposal }): FieldStageVisualState  // "upcoming" when lc null
fieldStageVisualStateFor(lc: Lifecycle | null, detail, name): FieldStageVisualState                                                // "upcoming" when lc null
recapVisibleFor(lc: Lifecycle | null, state): boolean                 // false when lc null; else lc.stateOrder.indexOf(state) > 1
```
`ceilingOptionsFor` slices `lc.stateOrder` from `floorState` and labels via `stateLabel(id)` — exactly
today's behavior. `lifecycle.ts` imports `stateLabel`/`fieldSlot` + the `FieldStageVisualState` type from
`ui.ts`; `ui.ts` will NOT import from `lifecycle.ts` (no cycle).

### 3.2 The manifest resource (`web/src/lib/manifest.svelte.ts`)

Fetch `GET /api/ticket-types` through the existing keyed resource cache under key `"ticket-types"`, like
`board`/`projects`. Fetched once on first subscribe; never invalidated (no event maps to it).
```
manifestResource(): ResourceHandle<TicketTypesResponse>   → resource("ticket-types", signal => fetchJson("/api/ticket-types", { signal }))
lifecycleFor(response, typeId): Lifecycle | null           → find types[].type_id === typeId; buildLifecycle(entry); memoize by typeId keyed on the response object.
```
Routes create the handle, `onDestroy(() => manifest.dispose())` (a WRAPPING arrow — `dispose` uses `this`,
so an unbound `handle.dispose` reference loses its receiver and throws; matches the existing route pattern —
Codex F4), and derive `lc` from the RESOURCE, not the markup-local `detail` (which is only bound inside
`{#if ticket.data}` / the review entry block — Codex F2):
`let lc = $derived(lifecycleFor(manifest.data, ticket.data?.ticket_type))` (ReviewRoute keys on the current
entry's detail resource). Manifest loads in parallel with the ticket.

**Loading vs error vs unknown-type are DISTINCT, not all collapsed to null-empty (Codex F3).**
`lifecycleFor` returns null for both "still loading" and "type absent from a loaded manifest"; the ROUTE
must tell them apart via `manifest.loading` / `manifest.error` + whether the loaded manifest contains the
type:
- manifest still loading → the pre-data frame (fields/leash empty), same as before `ticket.data` arrives.
- manifest ERRORED (`/api/ticket-types` 500) or the ticket's `ticket_type` is ABSENT from a LOADED manifest
  → surface an explicit error (`ErrorLine` / empty-with-reason), NOT a silently-empty ticket and NOT a
  stuck "Loading approval…" in ReviewRoute.

## 4. Per-file wiring

- **`ui.ts`** — delete constants `FIELD_NAMES`/`STATE_ORDER`/`GATING_FIELD`/`GATED_STATE`/`ADVANCE` and the
  six functions (moved to `lifecycle.ts`). Keep `stateLabel`/`labelize`/`fieldSlot`, status/format helpers,
  and the `FieldStageVisualState`/`TicketStageVisualInput` types.
- **`lifecycle.ts`** (new) — §3.1.
- **`manifest.svelte.ts`** (new) — §3.2.
- **`TicketRoute.svelte`** — add `manifestResource()` + dispose; `lc = $derived(lifecycleFor(...))`; field
  loop `{#each lc?.fieldIds ?? [] as name}`; pass `lifecycle={lc}` into `TicketStageSection`; recap gate via
  `recapVisibleFor(lc, state)`; leash `options={ceilingOptionsFor(lc, detail.state)}`; **worker pill** (§5).
- **`TicketStageSection.svelte`** — add `lifecycle` prop; route the three internal calls through
  `gatingFieldFor`/`fieldIsPassedFor`/`advanceTargetFor`; null `lifecycle` → non-gating/not-passed/no-next
  (same as before data). No markup/id change.
- **`ScopePairPicker.svelte`** — add `lifecycle` prop; `ceilingOptionsFor(lifecycle, newState || lifecycle.ceilingRange[0])`, guarded.
- **`ApprovalBlock.svelte`** — add pass-through `lifecycle` prop → `<ScopePairPicker {lifecycle} …>`.
- **`ReviewRoute.svelte`** — add `manifestResource()` + `lc` per current detail; `approvalField`/`isStale`/
  stage-state via the `*For` variants; pass `lifecycle={lc}` to `TicketStageSection`.
- **`BoardRoute.svelte`** — drop `gatingField`/`ticketStageVisualState` imports; reimplement
  `currentStageField`/`currentStageState` from 4a's card fields (`gating_field`, `is_done`, `ticket_status`,
  `has_pending_proposal`) — identical classification branches, no manifest fetch on the board. Keep a single
  isolated done-card `data-stage-field` fallback matching `test_board_stage_indicators.py`'s expectation
  (read the test to confirm the literal; cosmetic, not a lifecycle table).

## 5. The worker pill (display-only)

In `TicketRoute.svelte` `.ticket-facts`, right after `<span data-implementer>…</span>`, a **non-editable**
`Pill` (not `EnumPill`):
```svelte
<Pill keyLabel="type" data-ticket-type={detail.ticket_type}>
  {lc?.typeLabel ?? labelize(detail.ticket_type)}
</Pill>
```
Same `.pill` styling as `due`/`sprint`; no `<select>`, no `onChange`, no mutation (owner decision 2). Label
= manifest `m.label`; `labelize(ticket_type)` fallback for the pre-load frame. `data-ticket-type` for tests.
The only new visible element in the ticket.

## 6. `types.ts`
- `TicketDetail`: add `ticket_type: string;` (payload carries it; TS type omits it).
- Board card is already `AnyRecord`, so `card.ticket_type`/`is_done`/`gating_field` typecheck.
- Re-export `TicketTypesResponse` for the resource fetch typing; import `Lifecycle`/manifest types directly
  from `lifecycle.ts` at call sites.

## 7. The synthetic-manifest unit test (owner decision 1)

New `web/tests/lifecycle.test.mjs`, following the `file-preview.test.mjs` harness (transpile `ui.ts` →
`ui.mjs` and `lifecycle.ts` → `lifecycle.mjs` into one temp dir). **Codex F5: `transpileModule` PRESERVES
the extensionless `./ui` specifier and Node ESM will NOT resolve it to `ui.mjs` — the harness MUST rewrite
the emitted import (`from "./ui"` → `from "./ui.mjs"`) before writing `lifecycle.mjs`, or `npm test` fails
with `ERR_MODULE_NOT_FOUND` before any assertion.** Add the file to `web/package.json`'s `test` script (it
enumerates files) and to `./verify`'s frontend test list if it enumerates them.

*Part A — coding byte-identity (the bar as an assertion):* hardcode today's `STATE_ORDER`/`GATING_FIELD`/
`FIELD_NAMES`/`ADVANCE` + the coding manifest literal; assert `lc.stateOrder`/`fieldIds`/`gatingField`/
`advance` deep-equal them; assert `ceilingOptionsFor(lc,"needs_success")` yields the **lowercase**
`stateLabel` labels (`"needs success"` … `"done"`); spot-check `ticketStageVisualStateFor` classic cases.

*Part B — a synthetic SECOND type proves variability:* build a two-type response
`{types:[codingLiteral, researchLiteral]}` (research: stages needs_brief→needs_findings→needs_writeup→done;
fields brief/findings/writeup) and obtain `lc2 = lifecycleFor(response, "research")` — exercising the actual
LOOKUP + memoization, NOT `buildLifecycle(research)` directly (Codex F6: a direct build would pass even if
`lifecycleFor` always returned/memoized the first type, while research tickets silently render coding).
Assert `lc2` renders ITS lifecycle (fieldIds, gatingField, advanceTarget, ceilingOptions values,
fieldIsPassed, typeLabel "Research", recapVisible); assert `lifecycleFor(response, "coding")` still yields
coding's lifecycle unchanged (the selector picks the right type; building lc2 doesn't mutate lc). Proves the
render logic adapts without shipping a second type; production stays coding-only.

## 8. Byte-identity for coding — e2e that must stay green unchanged
- `test_flows_a.py` / `test_flows_b.py` — leash `[data-scope-ceiling].input_value()` = state ids,
  `[data-scope-atcap]` values, `data-field` sections. Preserved (ids/values derive identically).
- `test_board_stage_indicators.py` — card `data-stage-field`/`data-stage-state`/`data-marker`. Preserved by
  driving the current-stage mark from 4a's card fields with identical branches; confirm the done-card
  `data-stage-field` literal.
- `test_blockers_frontend.py`, `test_ticket_file_previews.py` — `data-field` sections; unaffected.
- `test_frontend_event_mapping.py` — untouched (no event mapping added).
Every DOM contract the e2e checks is keyed on stage/field **ids**, which the coding manifest reproduces
exactly; the one visible label (leash) keeps `stateLabel(id)`.

## 9. Files touched (ordered)
1. `web/src/lib/ui.ts` — remove lifecycle constants + six functions; keep helpers/types.
2. `web/src/lib/lifecycle.ts` (new).
3. `web/src/lib/manifest.svelte.ts` (new).
4. `web/src/lib/types.ts` — `ticket_type` on `TicketDetail`; re-export `TicketTypesResponse`.
5. `web/src/components/TicketStageSection.svelte`.
6. `web/src/components/ApprovalBlock.svelte`.
7. `web/src/components/ScopePairPicker.svelte`.
8. `web/src/routes/TicketRoute.svelte` — + worker pill.
9. `web/src/routes/ReviewRoute.svelte`.
10. `web/src/routes/BoardRoute.svelte`.
11. `web/tests/lifecycle.test.mjs` (new).
12. `web/package.json` — add the test file.

## 10. Risks / staying surgical
- **Signature-change ripple (top risk):** the `*For` first-arg change ripples into 6 files incl. two leaf
  components + a pass-through parent. Mitigation: thread one `lifecycle` prop route→leaf; `svelte-check`
  flags every missed site; `*For` naming prevents a half-migration.
- **Leash label regression:** `ceilingOptionsFor` keeps `stateLabel(id)`; asserted in §7A.
- **Board done-card `data-stage-field`:** single isolated fallback matching the e2e (read the test first).
- **Pre-load vs error vs unknown-type (Codex F1/F3):** the `*For` helpers are null-safe, so a null `lc`
  while `manifest.loading` renders the pre-`ticket.data` frame (empty fields/leash) — fine. But a manifest
  ERROR or a `ticket_type` absent from a LOADED manifest must render an explicit error, NOT a silently-empty
  ticket and NOT a stuck "Loading approval…" in ReviewRoute — the routes own the loading/error/unknown
  distinction (`manifest.loading`/`manifest.error` + type-present check). Worker pill falls back to
  `labelize(ticket_type)` for the pre-load frame.
- **No board reshape** (columns stay project-grouped), **no worker behavior** (pill has no `onChange`),
  **no second production type** (synthetic manifest only in the test), **no backend/contract/endpoint change**.
- **Completeness test** green by construction (no event kind, no `eventMapping.mjs` entry).

## 11. Verification gates
- `npm --prefix web test` (incl. `lifecycle.test.mjs`) — coding byte-identity + synthetic second type.
- `npm --prefix web run check` (svelte-check) — catches every un-migrated call site + the `ticket_type` add.
- `npm --prefix web run build` — clean.
- `./verify` — the §8 e2e set + the event-mapping completeness test stay green unchanged.

## Open confirm-at-implementation
- The exact `data-stage-field` value `test_board_stage_indicators.py` expects for a done card (pins the
  board's single cosmetic fallback literal).
- `recapVisibleFor` generalization (`index > 1`) vs an index-past-default-ceiling form — coding-identical
  either way; pick the simplest that reads honestly.
