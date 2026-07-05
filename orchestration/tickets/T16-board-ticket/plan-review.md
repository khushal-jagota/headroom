# T16 plan review — codex findings + orchestrator dispositions

Raw transcript: `plan-review-raw.txt` (codex exec, 207k tokens). Verdict: NEEDS-CHANGES, 7 findings
(3 blocker, 4 should-fix). Codex found **no endpoint path/method/body-shape mismatches**.
Every finding is accepted; fixes are recorded as binding amendments appended to plan.md §10.
T15 landed mid-review, so the amendments also replace plan §7's assumed interfaces with the
real landed signatures (read directly from assets/components.js).

## Dispositions

1. **Shell script tags absent from _SHELL (blocker) — ACCEPT.** Resolution: the orchestrator
   (not the implementer) adds the two `<script>` tags to `_SHELL` in `src/planner/core/server.py`
   after `/assets/app.js`, BEFORE the smoke runs — exactly the pattern already applied for T15
   (its `screens-day.js` / `screens-review.js` tags are in `_SHELL` now). The smoke asserts both
   tags are present in `GET /` and both files serve 200, so it fails loudly instead of silently
   rendering placeholders. The implementer's diff never touches server.py (also settles finding 7).

2. **grantControl must not redefine the T15 grant-pair picker (blocker) — ACCEPT.** D11 #8 says
   the picker is "embedded in proposal card **and grant control**". Amended: `grantControl`
   composes `Planner.components.grantPairPicker(currentState, onChange)` and adds only a
   current-grant text line and a Save button. `getGrant().next_ceiling === "none"` maps to
   `ceiling = currentState` for `POST /grant`. No duplicate select/radio internals in T16.

3. **requireGrant on non-gating proposals (should-fix) — ACCEPT.** Amended:
   `requireGrant = (field === Planner.components.gatingField(detail.state))` (helper now exported
   by T15's additions); `newState = Planner.components.advanceTarget(detail.state, detail.ceiling)`.
   Non-gating proposal cards render with `requireGrant: false` (no picker, Accept immediately fireable),
   matching resolution.py's non-gating branch which ignores the grant.

4. **`[data-screen="board"]` smoke wait collides with the nav link (should-fix) — ACCEPT.**
   The app-shell nav anchors carry `data-screen` too (components.js appShell). Amended: all smoke
   waits use `section[data-screen="..."]` (screen roots are `<section>`, nav links are `<a>`).

5. **Smoke "sets" the at-cap container (blocker) — ACCEPT, superseded by finding 2.** With the
   T15 picker embedded, the radios are real inputs inside its `[data-grant-atcap]` container
   (T15's actual spelling — not the plan's assumed `data-grant-at-cap`). The smoke clicks
   `... [data-grant-atcap] input[value="stop"]`. Inventory updated.

6. **Link row `from → far-end` renders target-side rows wrong (should-fix) — ACCEPT.** The demo's
   `t8 blocks t5` makes the current ticket the target on t5's screen. Amended: rows always render
   `kind: from_id → to_id`; each endpoint that is a ticket id other than the current one is an
   anchor to its ticket screen.

7. **Smoke depends on wiring outside owned files (should-fix) — ACCEPT via 1.** The _SHELL edit
   is explicit orchestrator glue done before the smoke, outside the implementer's diff; the smoke
   does not inject scripts (T17 chose injection; T16 follows the T15 precedent since the shell
   demonstrably receives tags at integration).

## T15 interface reconciliation (plan §7 assumptions → landed reality)

Read from assets/components.js after T15 landed:

- `proposalCard(opts)` — opts `{proposal, requireGrant, newState, onAccept}`. The card assembles
  the accept payload ITSELF: `{edited_body?}` only when the textarea differs from the proposal
  body, plus `{next_ceiling, at_cap}` only when `requireGrant`. `onAccept(payload)` must forward
  the payload verbatim to `POST /api/tickets/{id}/accept/{field}` and return the Promise.
  Internal attrs: Accept `[data-accept]`, quick-edit textarea `[data-edit]`, picker select
  `[data-grant-ceiling]`, at-cap radios inside `[data-grant-atcap]`. No `opts.field` label param
  (plan assumed one — dropped; the hosting field panel provides the label).
- `grantPairPicker(newState, onChange)` — positional args; returns a node with `.getGrant()`
  → `{next_ceiling, at_cap}` or `null` until both halves are chosen. Options: "No further"
  (`"none"`) + every STATE_ORDER state ≥ newState.
- `chatPanel(entityId, opts)` — positional `(entityId, {available})`. The SCREEN fetches
  `GET /api/chat/{entityId}/status` first (day-screen precedent, screens-day.js) and passes
  `available`; the panel renders its own offline notice when false. Input source attrs:
  `[data-chat-input]` textarea, `[data-chat-send]` button; messages `[data-chat-msg="you"|"planner"]`.
  Replies paint locally (not WS-flush-driven) — smoke waits for the echo text, no flush needed.
- Now-exported helpers T16 must REUSE, not redefine: `STATE_ORDER`, `gatingField`, `advanceTarget`,
  `quietLine`. Private-to-components.js helper `bindMutating(button, container, run)` exists for
  the mutating-button discipline (stays disabled on success) — the four T16 components use it.
  The plan's proposed private `STATE_ORDER` const and `inFlight` helper are DROPPED as duplicates.
