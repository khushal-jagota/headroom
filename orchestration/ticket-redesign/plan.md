# Plan — wire the ticket redesign into the app (rev 2, post Codex review)

## Objective
Restructure the ticket detail screen (`assets/screens-ticket.js`) to the redesigned layout and
introduce its shared interaction primitives. **Frontend refactor + one small resolution-engine
slice (Decision B).** The only backend/API surface change: **one new human-edit route
(`PUT /value/{field}`), one new event kind (`field_value_edited`), and a `dropped`-guard bugfix in
`decide_accept`** — no other endpoint or JSON-shape changes. `./verify` stays `VERIFY: 36/36 PASS` throughout — including e2e items 24, 25,
26, 30, 31 which assert on the ticket screen.

## Structural source of truth: the mockup
`orchestration/ticket-redesign/mockup.html`. Implement its **structure and interaction**, not its
placeholder palette/type. (One known mockup shortcut: it clones the approval `<template>` into
both the top slot and the Plan section — the implementation follows this plan's **single editable
approval + read-only gating mirror** rule instead; the double-render is a mockup convenience, not
the contract.) What it fixes (top of the screen):
- Order: header (title + metadata pills) → **Recap** → **the Approval** → the ticket's fields as
  **collapsible sections**. No section labels for the approval or the field list; no divider line
  between recap and the approval, nor above the first field.
- **The Approval** = whatever currently needs the human, in full, on a **raised surface**
  (background, no border). A **Note** sits inside on a **recessed surface** with "Note" as a header.
- **Collapsible fields**: `<details>` per field — mark (✓/●/○) + name + chevron, collapsed by
  default, body directly below (not inset). Lines only *between* rows.
- **Inline editing**: one shared hook, contenteditable, no affordance, blur/⌘Enter save, Esc revert
  — for the fields that persist directly (recap, notes, title, metadata pills, settled values).
  The **approval body is the exception**: it is a **draft** that persists only on Approve.
- **Chat**: plain-text agent, bubble human, no labels/header.
- Surface rule everywhere: **background or border, never both**; **no vertical lines; no insets.**

**Reconciliation with SPEC §10.4 (the mockup omits these; the real screen keeps them, tucked):**
the mockup drops links/runs/events/grant/day-sprint/copy. SPEC §10.4 *requires* the Ticket screen
to carry state control, ceiling/at-cap grant, **Copy ticket**, links, day/sprint assignment,
**run history**, event log, and chat; e2e item 30 asserts a running run + `running-claim` marker
on this screen. So they are **not removed** — they live **below the fields as collapsed
reference disclosures** (`<details>`), de-emphasized but present and functional. That satisfies
§10.4 and the e2e suite while keeping the clean recap→approval→fields top the owner wants.

## Non-goals / deferred
- The wholesale re-render (task #9). Not touched; no editing guard. Inline edits **and the
  approval body draft** (both unsaved-local until they persist) are only clobbered if a background
  event lands mid-type (mostly when an agent is working that same ticket); implementers must not
  treat the approval draft as protected — it is deferred with the inline-edit case, not guarded.
- Review/Day/Board/Backlog adopting the primitives (follow-up; Review reuses `approvalBlock` next).
- Sprint page redesign.

## Interaction → endpoint map (verified against api.py)
| UI action | Endpoint | Payload / notes |
|---|---|---|
| Edit title/project (human-only) + priority/deadline/sprint (agent-permitted, claim-validated) pills | `PATCH /api/tickets/{id}` | only `title`/`project` are `reject_agent_fields` human-only (`api.py:287`); `priority`/`deadline`/`sprint_id` stay agent-permitted; `project`/`sprint` edits are rejected when the ticket is parented (derived from its item) |
| State pill → a non-dropped state | `POST /api/tickets/{id}/state` | `{to}`; `decide_state_jump` rejects `dropped` |
| State pill → `dropped` | `POST /api/tickets/{id}/drop` | separate route |
| Unblock (if `auto_blocked`) | `POST /api/tickets/{id}/unblock` | |
| Edit recap (inline, blur-save) | `PUT /api/tickets/{id}/recap` | `{body}`; offered **only past `needs_success`** (§3.3; the server rejects recap in `needs_success`/`dropped`, `admission.py:63`) — the UI hides the recap editor there |
| Edit a field note / result review-notes (inline) | `PUT /api/tickets/{id}/notes/{field}` | `{note}` |
| Change grant directly | `POST /api/tickets/{id}/grant` | `{ceiling, at_cap}` |
| **Approval — pending gating proposal**: Approve (with edits + grant) | `POST /api/tickets/{id}/accept/{field}` | `{edited_body?, next_ceiling, at_cap}`; **draft-until-Approve**, not blur-save. The grant pair is required **only** for the current gating field; a non-gating accept omits it |
| **Approval — `needs_review`**: Approve the result | `POST /api/tickets/{id}/approve` | no grant; terminal → `done` (§4.4.5) |
| Copy ticket | `GET /api/tickets/{id}/copy-text` | existing |
| Chat status / send (side rail) | `GET /api/chat/{id}/status` · `POST /api/chat/{entity_id}/send` `{text}` | existing (`chat/api.py:21`); preserve offline-state + send behavior, not just styling |
| Links add / remove | `POST /api/links` · `DELETE /api/links?from_id&to_id&kind` | claim-**carrying** agent writes validated; plain agents pass (`api.py:490`) |
| Day add / remove ticket | `POST /api/day/{date}/tickets` · `DELETE /api/day/{date}/tickets/{ticket_id}` | claim-carrying agent writes validated; plain agents pass (`days/api.py:135`) |
| **Edit a settled field value (Decision B)** | **`PUT /api/tickets/{id}/value/{field}` (NEW)** | `{body}`; human-only; see below |

## Decision B — editing a settled field value (revised per Codex)
A field's `value` is written only by the resolution engine (§4.2). The engine's non-gating accept
branch (`resolution.py:173-186`) already writes `value` with no state change — but requires a
pending proposal. Add a dedicated, tightly-guarded human-edit path:
- `resolution.decide_edit_value(ticket, field, new_body, actor) -> Decision`:
  `admission.require_human`; `validate_body`; **require `slot.value is not None` AND
  `slot.proposal is None` AND the field is already *passed*** — i.e. the state that field gates
  strictly precedes the ticket's current state in the §4.1 order (success←needs_success,
  approach←needs_approach, plan←needs_plan, result←in_progress). Reject otherwise
  (`ErrorCode.validation`). That single "passed field" rule subsumes every hazard Codex raised: it
  forbids editing an unset value, a field carrying a live proposal, the **current gating field**,
  and any **future** field (which a later agent proposal could overwrite via auto-advance after a
  backward state-jump). It correctly still allows editing the settled `result` in `needs_review`
  and `done` (in_progress precedes both). Add a small pure helper `machine.field_is_passed(field,
  state)` for the ordering; it (and the route) **reject `dropped`** with `ErrorCode.validation`
  (the linear `STATE_ORDER` excludes `dropped`, so `state_index` is undefined there). Write
  `value = new_body`, keep `notes`, leave state/ceiling untouched.
- **New event kind** `field_value_edited` added to `core/contracts.py` `EventKind` (contracts-first,
  §14) with payload `{field, body}` — do **not** reuse `proposal_accepted` (it means "a pending
  proposal was accepted"; reusing it makes the log lie). The `eventLog` component (T3) must render
  this new kind so it appears in the ticket's tucked event-log disclosure.
- `data.edit_field_value(...)` applies the Decision via the sole appender `_apply_decision`.
- Route `PUT /api/tickets/{id}/value/{field}`, **human-only** (`reject_agents`), body marshalled via
  a new `ValueEditBody` TypedDict in `tickets/contracts.py`.
- **Lead spot-checks the resolution change directly** + Codex diff review + unit test: at
  `needs_plan`, edit settled `success` → value updated, `field_value_edited` logged, state/ceiling
  unchanged; a field with a live proposal → rejected; an unset field → rejected; agent → forbidden.

## The Approval component (single instance, two modes)
`approvalBlock` renders **once**, prominently, right after recap:
- **Gating-pending mode** (a proposal on the current gating field): shows the proposal body as an
  editable **draft** (local only), the Note (recessed), and **Approve** + inline ceiling/at_cap
  pickers. Approve → `POST /accept/{field}` with `{next_ceiling, at_cap}`, and `edited_body`
  **only when the draft differs from the original proposal body** (omit/null for an unedited
  accept so `proposal_accepted.edited` stays honest — matching the current `proposalCard`).
- **needs_review mode**: shows the result value + review-notes; **Approve** → `POST /approve`
  (no grant, terminal). (No pending gating proposal exists in this state.)
- **No second editable copy of the *gating* proposal.** The current gating proposal is the single
  editable draft, in `approvalBlock`; the **gating** field's collapsible section, when expanded,
  shows it **read-only** (a mirror), so there is exactly one live draft (Codex: two drafts desync
  without a store). **A pending proposal on a *non-gating* field is NOT mirrored read-only** — the
  resolution engine supports accepting it with no state change (`resolution.py:173`) and §4.4.4/
  §10.4 require it stay resolvable, so that field's section keeps full accept/edit controls (a
  resolvable proposal card, no grant pair).

## Phased tickets (contract-scoped; dependencies noted)
Order: **T1, T2 parallel** → **T3** → **T4** → **T5**.

### T1 — Token re-theme (`assets/tokens.css` only)
Swap token *values* to the mockup's warm surfaces + widened five-role type scale, mapped onto the
existing categories (`--surface-*`, `--text-*`, `--accent-*`, radius/motion/spacing). **No new
token categories; still five type sizes** (PRINCIPLES). Acceptance: `./verify` 36/36 (no e2e asserts
colors); every screen renders (manual pass).

### T2 — Backend slice for Decision B
Files: `tickets/logic/resolution.py`, `tickets/data.py`, `tickets/api.py`, `tickets/contracts.py`,
`core/contracts.py` (add `field_value_edited`). **Also harden `decide_accept` with a `dropped`
guard** — today a `dropped` ticket with a pending non-gating proposal can still be accepted (writes
`value`, no state change); reject accept when `ticket.state is dropped`
(**checked first, before the proposal/gating-field branches**) + add a drop-with-pending-proposal
test. Plus **two** new tests in `tests/unit/` (new surface,
not item 1–36 tests): (a) a pure-logic test of `decide_edit_value` (a settled/passed value edits
successfully; rejects unset value, live-proposal, the gating field, a **future field that already
holds a value + no proposal** (the backward-state-jump hazard), a **`dropped` ticket**, and an
agent actor); (b) an
**API-level** test of
`PUT /api/tickets/{id}/value/{field}` via TestClient — `ValueEditBody` marshalling, `reject_agents`
→ 400 `agent_forbidden`, bad `field` → validation error, the response shape, **and that
`GET /api/tickets/{id}/events` then contains a `field_value_edited` `{field, body}` row** (proving
the `_apply_decision` appender path, `data.py:76`) — since the api
layer's custom dict-marshalling (`api.py:308-380`) is untested by pure logic alone. Acceptance:
both tests green; `./verify` 36/36; ruff/mypy clean; Codex diff review vs §4.2/§4.4/§14 finds no
violation; lead spot-check of the resolution change; **record the new human write-model capability
(`PUT /value/{field}` + `field_value_edited`) in `decisions.md`** as a documented extension of the
§4/§9 human write surface (value is still written solely by the resolution engine).

### T3 — Shared primitives (`assets/components.js`), inventory first
**First** record/confirm the added component inventory in `decisions.md` (SPEC §10, PRINCIPLES).
Add (old components stay; other screens use them):
- `inlineEdit(el, {getValue, onSave, multiline, markdown})` — the one hook. For **markdown** fields
  (recap, notes, field values) the resting `el` shows the **rendered** markdown (via
  `markdownBlock`, §10.4) and swaps to a raw source editor on focus; for **plain scalars**
  (`title`, §3.3) it shows/edits plain `textContent`, no markdown render. In both, the editor is
  seeded from **`getValue()` — the raw string straight from the JSON, NEVER reconstructed from the
  rendered DOM** (rendered markdown can't round-trip links/code/lists). On
  blur or ⌘/Ctrl+Enter it calls `onSave(rawText)` → the mapped endpoint; Esc reverts; unchanged =
  no-op; re-renders on save; `data-ph` placeholder for empty. No editing guard (task #9). **On a
  rejected save, surface the structured error via `C.errorLine(err)` and keep the raw edit surface
  open — never a silent "looks-saved" failure** (matches the current `fieldEditor` contract). Used
  for direct-persist fields (recap, notes, title, settled values); metadata uses the pill controls
  below; NOT the approval body.
- `approvalBlock({mode, field, proposalBody, note, ceilingOptions, atCapOptions, onApprove, onNoteSave})`
  — the two-mode component above. The **approval body** is a local draft — a
  **raw string seeded from `slot.proposal.body`** (never `textContent`/DOM-derived); any rendered
  markdown preview swaps to a raw editing surface, so `onApprove` sends raw `edited_body` and
  preserves markdown links/lists/code. It persists only on `onApprove`. The **note is directly editable** via
  `inlineEdit`→`onNoteSave` (→ `PUT /notes/{field}`), persisted on blur like any other note (not a
  draft): since the gating field's collapsible mirror is read-only, the approval is the note's one
  editable home, keeping notes editable per §4.2/§10.4. Single instance.
- `collapsibleField({mark, name, body})` — native `<details>`, summary = mark+name+chevron, body not
  inset, first-child no top border.
- `enumPill({value, options, onChange})` — pill (background only) + transparent native `<select>`,
  for the **fixed-enum** metadata: `state` (non-dropped → `POST /state`; `dropped` → `POST /drop`;
  the pill **omits the current state** as an option and offers `dropped`/Drop **only when allowed**
  — never for `done` or already-`dropped`, since `decide_state_jump` rejects same-state and
  `dropped` and `decide_drop` rejects `done`/`dropped`), `priority`, `project`. **Not** one-size-fits-all: `deadline` is a **date input** with a
  clear-to-null option (nullable ISO date, not an enum); `sprint` is a **select populated
  dynamically** from `GET /api/sprints`. `sprint` renders **read-only** when parented,
  using `detail.effective_sprint_id` (the detail JSON exposes it). `project` is `null` on a
  parented ticket and the detail JSON does **not** expose an effective project — so the `project`
  pill is **editable on unparented tickets even when currently null** (offer a `(none)`/clear
  option — project is nullable), and is **omitted only when the ticket is parented**
  (`sprint_item_id` set; project is then derived + unsettable). Do not add
  a parent-item fetch to derive a project. The server rejects setting project/sprint on a
  parented ticket. Preserve this so no existing Ticket capability is lost.
- Restyle `chatPanel`: plain-text agent, bubble human, no header/labels — **preserve the e2e
  data-attributes** (`[data-chat]`, `[data-chat-offline]`, `[data-chat-send]`, `[data-chat-input]`,
  `[data-chat-msg="you"|"planner"]`).
- Keep/re-skin the reference primitives for the tucked disclosures (`runHistory`, `eventLog`,
  links, day/sprint, `grantControl`, `stateControl`, copy) — preserve their e2e data-attributes.
- **CSS (`assets/app.css`):** add the styles for the new components (approval raised surface, note
  recessed surface, collapsible fields, pills, chat, tucked disclosures) — **all values via
  `tokens.css`** — and neutralize the old ticket-specific panel/card/`field-editor` rules that use
  **background *and* border together** (they violate the mockup's one-or-the-other rule).
  Other screens' classes stay until they migrate.
Acceptance: `node --check assets/components.js` clean; `css check` (in `./verify`) passes; build-check green.

### T4 — Ticket screen rewrite (`assets/screens-ticket.js`)
Top: header (`inlineEdit` title + `enumPill`s + `running-claim`/`auto-blocked`/`blocked` markers) →
Recap (`inlineEdit`) → `approvalBlock` (gating-pending or needs_review by state) → `collapsibleField`
per field, driven by the **same `field_is_passed(field, state)` rule as Decision B** so the UI
never offers an edit the server must reject: a **passed** field with a value → value editable via
`inlineEdit`→`PUT /value/{field}` + note via `PUT /notes/{field}` (**except `result` in
`needs_review`**, whose value edit and Approve both live in the approval block, its own section
being the read-only mirror); a current/future field that
holds a value (e.g. after a backward state-jump) → value shown **read-only**; the **current gating
pending** field → **read-only mirror** of the approval above (single draft); a **non-gating
pending** field → **full accept/edit controls** (resolvable proposal card, no grant pair —
§4.4.4); **result** → review-notes. **Proposal precedence:** a pending proposal on a
**non-gating** field renders a **resolvable** proposal card (accept/edit, no grant) in its section
— so a `result.proposal` in `needs_review` stays resolvable. The **current gating** proposal is
editable **only** in `approvalBlock`; its own field section is the **read-only mirror** (never
duplicate accept/edit controls for it). A settled (proposal-less) `result` value in `needs_review`
is likewise the approval-owned read-only mirror. **On a `dropped` ticket (terminal), proposal / accept / value-edit controls render
read-only** (no advancing a dead ticket; the `decide_accept` `dropped` guard also rejects such
writes) — **but field notes stay editable** (§4.2: notes writable at any time; `PUT /notes` has no
dropped guard). **Every field's section carries exactly one inline `note`
editor** (`PUT /notes/{field}`) in **every** state — §4.2 notes are writable at any time, §10.4
requires them editable inline — **except** (a) the current-gating field **when it actually has
a pending proposal** (the approval block above then owns its note) — with **no** pending proposal
there is no approval block, so the gating field's own section owns its note editor as normal; and
(b) in `needs_review`, the **result** field — its value *and* review-notes are owned by the
approval block, the result section a **read-only mirror**. Net: every server note field has
exactly one editable home in every state. Chat stays the **side rail** (as in
the mockup), not a tucked disclosure, preserving the `data-chat*` attributes (§10.4, item 26). **Then tucked collapsed reference
disclosures**: Grant, Links, Day/Sprint, Runs, Events, Copy (SPEC §10.4). Keep the root
`section[data-screen="ticket"]` + `data-state` and all e2e-referenced attributes
(`[data-run-history] [data-run-row][data-run-status]`, `[data-marker="running-claim"]`, the review
accept selectors) so items 24/25/26/30/31 stay green. Stateless render over the same fetches; each
edit calls its endpoint and lets the existing invalidation re-render. Acceptance: `node --check`
clean; renders the seeded ticket; edits hit the right endpoints; markers/attributes present.

### T5 — e2e alignment (`tests/e2e/test_flows_a.py`, `test_flows_b.py`)
Update only **selectors** that moved; keep every **assertion on the spec's stated values** (§18.3
fence). Justify any behavior-affecting change in `decisions.md`. Acceptance: `./verify` →
`VERIFY: 36/36 PASS`.

## Verification & reviews (goal condition)
1. Codex reviews **this plan** (rev 2) → must reach `PLAN OK`.
2. Implement T1→T5 via Opus implementers with internal Codex diff reviews; lead integrates serially
   and spot-checks the resolution slice.
3. `./verify` fresh → 36/36.
4. Codex reviews **the implementation** (full diff vs this plan + mockup + SPEC/PRINCIPLES) and
   iterate until it reports no issues.

## Risks
- Re-render clobbering edits — deferred (task #9), not guarded.
- Token re-theme shifts every screen — intended, single-file, reversible.
- §10.4 reference elements + e2e attributes must survive the restructure — explicit in T3/T4.
- Resolution slice (B) — lead spot-check + Codex + unit test.
- Draft-until-Approve vs blur-save distinction in the approval — Codex checks in the impl review.
