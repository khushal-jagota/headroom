# T15 report — Day + Review screens

## What was built

SPEC §10 screens 1 and 2 on the T14 foundation, plus D11 components 7–10 and 16, composing the T14 primitives (panel remains the only box primitive; reviewCard wraps `panel()`).

- **`assets/components.js`** (additive block, clearly marked `T15 additions`, plus export-literal keys and the `Planner.chatInput` namespace):
  - Helpers: `STATE_ORDER`, `advanceTarget(state, ceiling)` (mirrors machine.py incl. the ceiling=done→done special case), `gatingField(state)`, `quietLine(text)` — mirrors used only to pick which grant options to OFFER; the server re-validates every grant.
  - `bindMutating` (private): the amendment-A3 discipline — disable on click, errorLine + re-enable on rejection, STAY disabled on success (the WS-flush re-render replaces the DOM; re-enabling would reopen the double-fire gap).
  - **7 `proposalCard`**: rendered proposal (markdownBlock), quick-edit textarea prefilled with the proposal body, Accept; `edited_body` sent iff the textarea differs from the fetched body (strict string, whitespace counts); hosts the grant picker when the accept is gating.
  - **8 `grantPairPicker(newState, onChange)`**: ceiling select with a `value="" disabled selected hidden` placeholder + "No further" (`none`) + every state at-or-beyond the newly entered state (exactly `resolve_grant`'s floor); two per-instance-named radios (stop/propose), none pre-checked. `getGrant()` is null until BOTH halves are explicit — Accept is mechanically unfireable without the pair, including the "nothing further" case.
  - **9 `planTree`**: root focus + child rows with visible status (`data-status` also drives the amber "proposed = awaiting the human" accent), per-node Accept (hidden on already-accepted nodes)/Invalidate, top Accept-all/Reject-all; child node refs are numbers ("root" for root) per the server contract.
  - **10 `chatPanel(entityId, {available})`**: message list rebuilt from module-scope transcripts (chat has no server-side message store — session key only — so this is conversation-render state, not a canonical cache; survives every flush re-render, resets on reload by design); drafts preserved across re-renders via `ctx.onInput`; offline → "gateway offline" notice, no input source. The §14 audio seam is the `Planner.chatInput` registry: a source is `factory(ctx) -> element` with `ctx.submit(text) -> Promise`, `ctx.initialText`, `ctx.onInput(text)`; audio later = its own folder + one `register()` call + one script tag, nothing else touched.
  - **16 `reviewCard`**: per-kind body — gating proposal (proposalCard + mandatory picker), `review` (result markdown + review notes + Approve, no grant), `status` (current → proposed chips + note + Accept, no grant) — plus Skip and, for tickets, the open-ticket link.
- **`assets/screens-day.js`** (new): brief, plan tree (or "(no plan)"), ordered day ticket list (entityRow + sibling Remove button = defer), Review entry with pending count (amber when non-zero, achromatic at 0), day chat panel. Chat entity id and mutating-route dates come from the server's `day.id` — never the browser clock.
- **`assets/screens-review.js`** (new): one-at-a-time walk, oldest-first straight from `/api/queues` (never re-sorted); per-entry detail fetch with staleness guard (advances within the fetched list — post-review fix); per-session skip memory keyed `entity_id:kind` (reload resets; wrap rule restarts at the oldest when everything is skipped); detail-fetch failure renders an inline error + Skip (nothing auto-hidden, no fetch loop); empty queue → `nothing waiting` quiet line.
- **`assets/app.css`**: four appended token-only sections (Day / Plan tree / Chat / Proposal-grant-review). Accent discipline held: amber = grant picker, primary actions, pending markers, non-zero review count, `proposed` plan nodes; Skip/Invalidate/Reject-all/Remove stay achromatic.
- **`src/planner/core/server.py`**: exactly two `<script>` lines appended to `_SHELL` after app.js.
- **`orchestration/tickets/T15-day-review/smoke.py`**: 10-check Playwright gate (below).

## The data-* attribute contract (stage-6 e2e selects on these)

Scoping caveat: T14 nav links also carry `data-screen` — always scope screen roots as `.screen[data-screen="day"]` / `.screen[data-screen="review"]`.

| Attribute | Element / value |
|---|---|
| `data-screen` | screen root div; `"day"` / `"review"` |
| `data-node` | plan node row; `"root"` or child position (`"0"`, `"1"`, …) |
| `data-status` | plan node row; `"proposed"` / `"accepted"` / `"invalidated"` |
| `data-accept` | per-node Accept button (inside `[data-node]`) AND proposalCard Accept (inside `[data-review-card]`) — disambiguate by ancestor |
| `data-invalidate` | per-node Invalidate button |
| `data-accept-all` / `data-reject-all` | plan tree top actions |
| `data-ticket-id` | day ticket row wrapper; the ticket id |
| `data-remove` | remove/defer button in a day row |
| `data-review-entry` | the Review link (Day side column) |
| `data-pending-count` | count pill; value = String(approvals.length), also its text |
| `data-chat-panel` / `data-chat-messages` | chat panel root / message list |
| `data-chat-msg` | one message row; `"you"` / `"planner"` |
| `data-chat-input` / `data-chat-send` | typed source textarea / Send button |
| `data-chat-offline` | the "gateway offline" notice |
| `data-review-card` | review card root (panel section) |
| `data-entity-id` / `data-kind` | review card root; entity id / `"success"|"approach"|"plan"|"result"|"review"|"status"` |
| `data-field` | review card root, gating kinds only (equals kind) |
| `data-edit` | quick-edit textarea (proposalCard) |
| `data-approve` | Approve button (kind `review`) |
| `data-accept-status` | Accept button (kind `status`) |
| `data-skip` | Skip button (reviewCard and the detail-error surface) |
| `data-open-ticket` | open-ticket anchor (href carries the target) |
| `data-grant-ceiling` | ceiling `<select>`; options carry native `value`: `""` (placeholder), `"none"`, wire state values |
| `data-grant-atcap` | radio-group container; radios carry native `value="stop"` / `"propose"` |
| `data-review-empty` | empty-queue quiet line |

## Pipeline record

plan.md (Fable planner) → plan-review.md (codex, 6 findings, ALL ACCEPTED, bound as plan §12 amendments A1–A6: no `withAttr`; `vanished` set deleted in favor of a non-hiding non-looping failure design; stay-disabled-on-success for every mutating control; innerHTML fence corrected to the new-code diff; smoke goto typo; plan-node count selectors) → implementation (Opus, all definition-of-done checks green first pass) → impl-review.md (codex, 2 findings, BOTH ACCEPTED AND FIXED by the orchestrator: P2 stale-wrap dead-end in the review walk → `show` now advances within the fetched list; P3 smoke missing the child `data-node` value assertion → added).

## Gate results (final state, all run fresh by the orchestrator after the fixes)

- `node --check` on components.js, screens-day.js, screens-review.js: clean.
- Fences: `grep -nE '\b(import|export|await)\b' assets/*.js` empty; `grep -n innerHTML assets/screens-day.js assets/screens-review.js` empty; CSS literal grep on app.css empty.
- `git diff src/planner/core/server.py`: exactly the two script lines.
- `.venv/bin/ruff check .` → All checks passed. `.venv/bin/mypy src/` → Success, 84 files. `.venv/bin/pytest tests/unit -q` → 81 passed.
- smoke.py (demo-seeded test-mode server, temp DB, fake adapters, headless chromium; second offline-gateway instance):

```
ok 01 — server up; demo ids resolved (t1=..., t4=..., t8=..., t5=..., item=...)
ok 02 — (a) day: brief, plan focus, 2 plan children, rows [t4,t8,t5], pending 1, chat present
ok 03 — (b) remove + accept-all: list [t4,t5,t8], every plan node accepted (DOM + API)
ok 04 — (c) chat: you + planner echo survive the session-created flush; session key persisted
ok 05 — (d) queue oldest-first: [t5 review, t1 success, fresh success, item status]
ok 06 — (e1) t5: review card (no grant/edit), skip advances, reload restores, approve -> done
ok 07 — (e2) t1: accept gated on both halves; none pins ceiling needs_approach; at_cap stop
ok 08 — (e3) fresh: prefill + edit-accept stores altered text verbatim; ceiling needs_plan
ok 09 — (e4) item: accept-status -> done; queue empty (data-review-empty); nav badge hidden
ok 10 — (f) offline instance: chat-offline notice, no Send, brief + 3 rows intact
SMOKE PASS (10 checks)
```

The smoke covers e2e-precursor behavior for items 24 (grant pair mandatory, "no further"+stop pins ceiling to the new state), 25 (edit-accept stores altered text verbatim with the (needs_plan, propose) grant), 28 (accept-all adds plan children to the day list), and the §11 chat contract (echo + offline notice).

## Deviations from ticket

1. **server.py `_SHELL` gained two script tags** — not in ticket.md's owned-files list, but required for the screens to load and explicitly directed by the T14 handoff ("add their script tags after app.js in `_SHELL`"). Two lines, nothing else.
2. **app.js untouched** (the dispatch mentioned "registering the two screens in app.js's registry block"): registration happens by the screen scripts calling `Planner.registerScreen` over the placeholders — overwrite-wins is the T14-designed mechanism; app.js's placeholder list already names day/review.
3. **Utility additions beyond the five D11 components**, flagged for sign-off per the ticket's new-component rule and hereby signed off by this orchestrator as utilities, not inventory: exported `quietLine`/`advanceTarget`/`gatingField`/`STATE_ORDER`; private `bindMutating`; the `Planner.chatInput` registry (the §14 seam itself). None duplicates an inventory component.
4. None otherwise: owned files only, additive-only in the shared files, no assertion weakened anywhere.

## Concerns for the integrator

- **Concurrent worktree writes to shared files.** T16/T17 work was already interleaved uncommitted in components.js and app.css while T15 ran (T17's `createForm` + Sprint/Backlog CSS, screens-sprint.js/screens-backlog.js present). T15's additions live in a clearly-marked block and layered cleanly (no key/class collisions; all T15 gates green over the combined state), but the one-writer-per-file rule was not upheld across tickets — worth serializing components.js/app.css merges for the remaining stage-5 work.
- **Mid-pick WS flush resets the grant picker/textarea** (accepted v1, plan §8.1): an unrelated event mid-interaction re-renders the review card and clears in-progress picks. Stage-6 e2e should act serially per card (the smoke does).
- Chat transcripts are session-transient by design (the server stores only the session key); reload clears them.
- Smoke determinism relies on normalizing demo-seed timestamps to fake-noon−600 and spacing setup filings via `/api/test/set-now` (frozen TestClock + unstable equal-second tie-break otherwise); reuse that pattern in stage-6 seeding.
