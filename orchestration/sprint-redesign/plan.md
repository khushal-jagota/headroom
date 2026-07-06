# Plan — wire the Sprint Tracking redesign into the app

> **Sequencing (hard):** this plan's *code build runs AFTER the ticket-redesign build commits.*
> It shares `assets/app.css` (and, via the router, `assets/app.js`) with that in-flight build —
> **no concurrent writes.** Start only once the ticket build has landed on `main`.
>
> **Scope (hard):** *only the Tracking page* (`#/sprint`) is designed and built here. **Kickoff and
> Review are FUTURE work** — each needs its own mockup + owner approval before its page is
> *redesigned*. This plan keeps them **reachable and functional** in the interim by relocating
> today's kickoff/review/addenda panels **as-is** onto sub-routes (a mechanical move, not a
> redesign), so nothing spec-required is dropped. See §"Kickoff / Review disposition".

## Objective
Replace today's single stacked Sprint screen with the approved **progressive-disclosure Tracking
page** (`orchestration/sprint-redesign/mockup.html`): sprint → items → tickets, calm at rest.
Frontend rewrite of `assets/screens-sprint.js` + one **read-only, contract-first** view enrichment
(per-item ticket rows) + a **minimal additive router hook** for the two future sub-routes. No new
write model, no new write endpoint. `./verify` stays **`VERIFY: 36/36 PASS`** throughout — the two
sprint e2e flows (items 32, 33) keep their data-attribute contract, so the rewrite is designed to
need **zero e2e edits** (fallback: one selector line — see e2e notes).

## Structural source of truth: the mockup
`orchestration/sprint-redesign/mockup.html` + `orchestration/sprint-redesign/principles.md`.
Implement its **structure and interaction**, not its inline placeholder palette/type (those already
live in the landed `assets/tokens.css`). The page, at rest: the sprint name (editable) + a dates
pill; **the bet stated once** (read-only echo of `primary_bet`); **the arc** (`Kickoff ✓ · Running ●
· Review ○` + a "N of M done" health count) doubling as nav to the future sub-pages; **live status
groups** (active / todo / blocked) as collapsed item rows; **settled groups** (done / deferred) and
**loose tickets** as group-level collapsed disclosures. Expanding an item reveals its tickets and
their states. No approval band, no accept/unblock controls, no chat rail (§11: a sprint has no chat
session), single centered column.

## Data / API decision (ST1) — one read enrichment, no new endpoint
**The gap:** the disclosure needs, per item, its **tickets as rows** — `{id, title, state,
priority}` + a link to `#/ticket/{id}`. Today `GET /api/sprint/current`
(`sprints/views.py:sprint_current_view`) gives each item a **`rollup`** (counts per ticket state)
and `blockers_cleared`, but **not the ticket rows**. SPEC §5/§10 item 5 require the rollup counts;
the redesign needs the rows *in addition*.

**Decision — eager embed in the existing current-sprint view (reuse, not a new endpoint):**
extend `sprint_current_view` so each item in `groups[...]` also carries:
- `tickets: [{id, title, state, priority}]` — ordered `created_at, id` (deterministic, matching the
  loose-ticket ordering already in that view). New helper `item_tickets(conn, item_id)` in
  `sprints/views.py`, a lightweight projection (**not** full `ticket_json`).
- `blocked_by_titles: [str]` — resolve the item's `blocked_by` ticket ids → titles, so the Blocked
  group's "blocked by …" chip (mockup) renders a name, not a raw id (§3.2 stores ids). Cheap
  `SELECT id, title` over `blocked_by`; empty when not blocked.

Keep `rollup` and `blockers_cleared` (still §10-required and used for the item count / cleared
chip). **Rejected — lazy `GET /api/tickets?sprint_item_id=X` per expand:** it forces client-side
open-state + a fetch per item + refetch-on-invalidation bookkeeping, which violates "the event feed
is an invalidation signal; the UI refetches JSON; no client-side state stores" (CLAUDE.md, §9).
Eager keeps it one fetch, stateless, invalidation-refetch-clean.

- **Owned file:** `src/planner/sprints/views.py` (read assembly only — no writer, no contract-shape
  change to `item_json`, which has no `conn`; the rows are added at the view-assembly layer).
- **Contract note (§14):** the current-sprint item JSON *shape grows* two read-only fields; record
  it in `decisions.md`/`DOCS.md` and the frontend consumes exactly that shape.
- **Lead spot-check + Codex** review the view against §5/§10; a unit/API test asserts the new shape.

## Interaction → endpoint map (verified against `sprints/api.py`)
| UI action (Tracking page) | Endpoint | Notes |
|---|---|---|
| Edit sprint **name** (inline, blur-save) | `PATCH /api/sprints/{id}` `{name}` | human-only (`reject_agents`, `api.py:299`); `name` is **never freeze-gated** (`frozen_group("name") → None`, `freeze.py`) — always editable |
| Open an item's tickets | — | native `<details>` disclosure; no request |
| Open a ticket | `#/ticket/{id}` | native anchor (`C.ROUTES.ticketPrefix`) |
| Arc → Kickoff / Review | `#/sprint/kickoff` · `#/sprint/review` | hash nav to the relocated sub-pages (router hook, ST2) |
| Read the bet | — | read-only echo of `sprint.primary_bet` (canonical editable copy lives on the Kickoff page) |

The Tracking page is **read + navigate** only (principles P2/P7): no proposal, accept, unblock,
freeze, or addendum controls — those are Review/Ticket/Kickoff surfaces. The only write it issues is
the inline name edit.

## Routing hook (ST2) + Kickoff / Review disposition
**Router constraint (found in `assets/app.js`):** `route()` accepts **exact shapes only** —
`#/<screen>` or `#/ticket/<id>`; anything with trailing segments is rejected as *"no such screen"*.
So the mockup's arc links `#/sprint/kickoff` and `#/sprint/review` **do not resolve today**.

**Minimal additive router hook (owned file `assets/app.js`):** mirror the existing `ticket`
special-case — accept an optional second segment for `sprint` where `sub ∈ {kickoff, review}`, set
`params.sub`, relax `badShape` for that shape, and keep `setActiveNav("sprint")` for all three. One
registered `sprint` screen keeps owning `#/sprint`, `#/sprint/kickoff`, `#/sprint/review`. No new
nav item, no change to any other route.

**Kickoff / Review disposition (keeps §10 item 5 whole — nothing dropped):** `screens-sprint.js`
dispatches on `params.sub`:
- `undefined` → **`renderTracking`** — the new design.
- `"kickoff"` → **`renderKickoff`** — today's `kickoffPanel` + `addendaPanel`, **relocated as-is**
  (addenda live here per principles: they amend the frozen kickoff plan, §3.1).
- `"review"` → **`renderReview`** — today's `reviewPanel`, **relocated as-is**.

The sub-pages keep their current (functional, un-redesigned) rendering behind the arc links until
their **own** redesign lands. **The future work is the visual redesign of Kickoff and Review** to a
new approved mockup + the design system — explicitly *not* in this plan; do not design their
internals here.

## Frontend structure (ST3) — what `screens-sprint.js` becomes
Rewrite the tracking render to the mockup; **relocate** (do not delete) kickoff/review/addenda into
the sub-route branches above. `renderTracking(root, res)`:
- **Header** — root `[data-screen="sprint"]`; a `.sprint-header` carrying the editable **name**
  (`.screen-title`) and the **dates** (`.sprint-dates`). *(Class names `sprint-header /
  screen-title / sprint-dates` are the e2e contract for item 33 — kept, restyled to the mockup.)*
- **Bet frame** — `.frame` with `markdownBlock(sprint.primary_bet)` (read-only).
- **Arc** — `.arc` strip: `Kickoff` (link to `#/sprint/kickoff`, mark `✓` iff `kickoff_frozen_at`
  set else `○`) · `Running` (`●` when kickoff frozen & review not) · `Review` (link to
  `#/sprint/review`, `✓`/`○` per `review_frozen_at`) · health `"X of M done"` (X = items with status
  `done`, M = total items — derived from `groups` on render). State-driven per P8.
- **Live groups** — active / todo / blocked, each `[data-status-group="<status>"]` with a whisper
  `.glabel`, **always rendered even when empty** (keeps item 32's ready selector stable — see e2e).
  Each item is a native `<details data-item-id>` (screen-local composition): summary = chevron +
  title `<span class="it entity-row-title">` + chips + count; body `.tkts` = ticket rows or an empty
  note. *(Title span carries `entity-row-title`, chips/count are **siblings** — item 33 compares
  exact `textContent`.)*
- **Settled groups** — done / deferred as group-level collapsed `<details class="sett">`; inside,
  the same item disclosures, each still `[data-status-group="<status>"] [data-item-id]` so item 33
  reads their titles (its `_texts` = `eval_on_selector_all`, **visibility-agnostic** — collapsed is
  fine).
- **Loose tickets** — a collapsed `<details class="sett">` whose body is `[data-loose]` with rows
  `<a class="tk" data-ticket-id>` (state · title `<span class="tt entity-row-title">` · priority).
- **Ticket row** — `<a class="tk" href="#/ticket/{id}">`: `.st` state (raw enum prettified;
  `.st.review`/`.st.done` color variants), `.tt` title, `.pr` priority.

### Reuse of the landed primitives (reuse hard, do not reinvent)
- `comp.inlineEdit(nameEl, {getValue, onSave})` → the sprint name (P7 in-place edit, amber caret,
  blur/⌘Enter save, Esc revert, `errorLine` on reject). **The one editable surface on the page.**
- `comp.markdownBlock(sprint.primary_bet)` → the bet frame.
- `comp.chip(variant, value)` → item chips: `priority`, `project`, `deadline`, `blockers-cleared`
  (the existing `blockers-cleared` marker chip). The "blocked by <name>" chip renders from
  `blocked_by_titles` (ST1) as a plain chip.
- `comp.errorLine` → fetch/save errors.
- **Disclosure pattern** — the item and settled-group disclosures are native `<details>` composed
  **in `screens-sprint.js`** (this file is owned by this build). They reuse the tokens + the
  `<details>` disclosure *idiom* the ticket build's `collapsibleField` established, but are **not**
  routed through `collapsibleField` (its summary is `mark+name+chevron`; the item summary is
  `chevron+title+chips+count` — a different composition, so forcing reuse would mean editing the
  shared signature). This keeps **`assets/components.js` untouched** — zero surface on the file the
  ticket build owns.

### CSS (ST3, owned file `assets/app.css` — additive, small)
Add one tracking block, **scoped under `[data-screen="sprint"]`** so generic selectors (`details.item`,
`.frame`, `.arc`, `.grp`/`.glabel`, `.chev`, `.it`, `.chips`, `.count`, `.tkts`, `.tk`/`.st`/`.tt`/`.pr`,
`details.sett`/`.glabel2`/`.gchev`, `.none`) can't collide with ticket rules or any other screen. **All
values from `tokens.css`** (`--surface-*`, the `--text-*` ladder, `--accent-done` for the ✓/done,
`--accent-bright` only on the arc `●` + the name caret, `--border-color` hairlines, `--radius-*`,
`--motion-*`). Reuse `.chip`, `.pill`, `.ed`, `.entity-row*` styling already present. The old
sprint-only tracking classes that the rewrite stops using (`.status-group*`, `.sprint-item`,
`.rollup`) may be removed in the same edit — `screens-sprint.js` is their only consumer and it is
rewritten; the kickoff/review sub-pages keep `.sprint-header`/`.sprint-field`/`.addendum*`, so those
stay. **Touch no ticket-specific rule.**

## §10 item 5 coverage (the Sprint screen's required elements — nothing silently dropped)
SPEC §10 item 5 requires: *kickoff fields; items grouped by status (with ticket rollup counts and
blockers-cleared flags); loose tickets; review fields (frozen states shown as locked).*

| Required element | Where it lives after this build |
|---|---|
| Kickoff fields (§3.1) | **`#/sprint/kickoff`** — today's `kickoffPanel`, relocated as-is (redesign = future) |
| Weekly addenda (§3.1, append-only) | **`#/sprint/kickoff`** — today's `addendaPanel`, relocated (amends the frozen kickoff) |
| Items grouped by status | **`#/sprint` (Tracking)** — live groups + settled disclosures, per-item ticket disclosure |
| …ticket rollup counts | **`#/sprint`** — item "N tickets" count; `rollup` retained in the JSON (ST1) |
| …blockers-cleared flags | **`#/sprint`** — `blockers-cleared` chip from `blockers_cleared` |
| Loose tickets | **`#/sprint`** — Loose-tickets disclosure |
| Review fields (frozen = locked) | **`#/sprint/review`** — today's `reviewPanel` (frozen chip/lock), relocated (redesign = future) |

Every element stays reachable in the running UI via the nav + arc links; only the *visual redesign*
of kickoff/review is deferred.

## e2e notes (`./verify` stays 36/36)
Two browser flows touch the sprint screen; **both are preserved by keeping the data-attribute + the
three class hooks** — the rewrite treats them as the DOM contract:
- **Item 32** (`test_flows_b.py::test_e32_sprint_live_status_and_loose`): needs
  `[data-status-group="active"]` (its **ready** signal, asserted while active is *empty*),
  `[data-status-group="todo"] [data-item-id]`, `[data-loose] [data-ticket-id]`, and live
  todo→active regrouping. **Kept green with no edit** by rendering the three live groups
  **always-present** (so empty `active` still resolves) and putting `data-item-id` on each item
  `<details>` (in-DOM even when collapsed). *Fallback if the owner prefers omitting empty live
  groups:* change only item 32's `ready` to `'[data-screen="sprint"]'` (a load-signal selector, not
  a value assertion — allowed by the §18.3 fence). Default plan = no edit.
- **Item 33** (`test_seed_e2e.py`): asserts `.sprint-header .screen-title` (name),
  `.sprint-header .sprint-dates`, `[data-status-group="{status}"] [data-item-id] .entity-row-title`
  per status (incl. `done`, which sits in a collapsed disclosure — its `_texts` is
  visibility-agnostic), and `[data-loose] [data-ticket-id] .entity-row-title`. **Kept green with no
  edit** by retaining those exact class names on the restyled DOM (title spans hold `entity-row-title`,
  chips/count are siblings so `textContent` stays exact).

So **T-ST4 is expected to be a no-op verification pass.** Any selector that genuinely must move is a
T5-style *selector-only* change (never a value assertion), justified in `decisions.md`.

## Phased tickets (contract-scoped; owned files; order ST1 → ST2 → ST3 → ST4)
ST1 and ST2 are independent (backend view vs. router) and may share the main worktree or run in
isolated worktrees; **ST3 depends on both**; ST4 verifies.

### ST1 — Data enrichment (`src/planner/sprints/views.py`)
Add `item_tickets(conn, item_id) → [{id,title,state,priority}]` (order `created_at, id`) and
`blocked_by` id→title resolution; embed `tickets` + `blocked_by_titles` on each item in
`sprint_current_view`. Read-only; no writer; `item_json` unchanged. **Acceptance:** a unit/API test
asserts the current-sprint JSON carries per-item `tickets` (shape + order) and `blocked_by_titles`;
`./verify` 36/36; ruff/mypy clean; Codex review vs §5/§10; lead spot-check; shape recorded in
`decisions.md`/`DOCS.md`.

### ST2 — Router hook (`assets/app.js`)
Additive `sprint` sub-route parsing (`params.sub ∈ {kickoff, review}`), `setActiveNav("sprint")` for
all three, exact-shape otherwise unchanged. **Acceptance:** `#/sprint`, `#/sprint/kickoff`,
`#/sprint/review` resolve (no "no such screen"); unknown `#/sprint/x` still rejects; `node --check
assets/app.js`; `./verify` 36/36.

### ST3 — Tracking rewrite + relocation (`assets/screens-sprint.js`, `assets/app.css`)
`renderTracking` per the mockup (reusing `inlineEdit`/`markdownBlock`/`chip`/`errorLine`; disclosures
composed in-screen); relocate `kickoff`/`review`/`addenda` rendering to the `params.sub` branches;
additive scoped CSS block. Preserve every e2e hook above. **Acceptance:** `node --check
assets/screens-sprint.js`; the css check inside `./verify` passes; the seeded/demo sprint renders
per the mockup (items collapsed, disclosure reveals tickets, arc reflects freeze state); `./verify`
36/36; `components.js` untouched.

### ST4 — e2e alignment (`tests/e2e/` — expected no-op)
Confirm items 32 & 33 pass unchanged; apply the single item-32 ready-selector fallback only if the
"omit empty live groups" variant is chosen. **Acceptance:** `./verify` → `VERIFY: 36/36 PASS`.

## Verification & reviews (goal condition)
1. Codex reviews **this plan** vs the mockup + SPEC §5/§10/§3.1/§3.2/§9/§14 + PRINCIPLES → `PLAN OK`.
2. Implement ST1→ST3 via Opus implementers with internal Codex diff reviews; lead integrates
   serially and **spot-checks the view enrichment (ST1) and the router change (ST2)** directly.
3. `node --check` (screens-sprint.js, app.js) + the css check + `./verify` fresh → **36/36**.
4. Codex reviews **the implementation** (full diff vs this plan + mockup + SPEC/PRINCIPLES) and
   iterate until it reports no issues.

## Risks
- **Shared-file writes (app.css, app.js).** Mitigated by the hard sequencing: build **after** the
  ticket redesign commits; changes are additive and CSS is `[data-screen="sprint"]`-scoped;
  `components.js` is left untouched. The ticket build does **not** touch `app.js`, so the router hook
  is collision-free.
- **Empty live groups vs. the calm mockup.** Default renders live groups always (zero e2e churn); the
  purer "omit empty" variant costs one selector line (item 32 ready). Owner's call — noted in
  `decisions.md`.
- **Kickoff/Review interim state** — they render un-redesigned behind the arc links until their own
  build. Flagged below as the biggest open question.
- **`blocked_by` is ticket ids, not names** — resolved to titles in the view (ST1) for the mockup
  chip; low stakes, deterministic query.
- **Collapsed `<details>` + e2e visibility** — verified: item 33 reads text visibility-agnostically;
  item 32's *visible* waits target live (non-collapsed) groups. Called out so implementers keep
  live groups un-collapsed.
