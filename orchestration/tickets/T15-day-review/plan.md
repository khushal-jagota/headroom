# T15 plan — Day + Review screens (stage 5, D11 items 7, 8, 9, 10, 16)

**Ticket:** orchestration/tickets/T15-day-review/ticket.md.
**Contracts read:** SPEC §10 (screens 1 Day and 2 Review), §4.4.7 (onward grant), §6.3 (day-plan tree), §11 (chat), §14 (structural rules, audio seam), §9 (API + invalidation), §15 (non-goals), §18.3 items 24/25/28/29; decisions.md D11 (component inventory — this ticket builds items 7, 8, 9, 10, 16 and composes items 1–6/17, never near-duplicating them); orchestration/tickets/T14-ui-foundation/report.md ("Concerns for the integrator" = the consumption contract); the T14 foundation on disk (assets/app.js, components.js, api.js, markdown.js, config.js, tokens.css, app.css) — **the foundation is law; nothing in it is reworked**. Every server fact this plan builds on is cited in §9 with file:line.

**Approach.** Two new classic scripts (`assets/screens-day.js`, `assets/screens-review.js`) register the real `day` and `review` screens over app.js's placeholders (overwrite-wins is the designed mechanism — app.js:26-32 registers placeholders for exactly these names and its comment says T15–T17 scripts, appended after it, re-register before DOMContentLoaded). Five new components land as **additive** entries in `assets/components.js`, in the file's existing style (`make`/`appendChildren`, `createElement`/`textContent` only — the codebase's single `innerHTML` stays in markdown.js). Screens are stateless renders: every action calls `Planner.api.fetchJson` and relies on the WS flush → `route()` → full re-render with fresh fetches (app.js:76-79); no optimistic UI anywhere. The only client state is transient UI state in module scope — chat transcripts + input drafts (components.js scope), review skip memory (screens-review.js scope) — never a cache of canonical entity JSON. `server.py` changes in exactly one place: two script tags in `_SHELL` after app.js. `app.css` gains new class rules, token-only. The gate is `smoke.py` in this ticket folder, modeled on T14's: in-process uvicorn, temp DB, demo seed, headless chromium, numbered `ok NN` checks, `SMOKE PASS`.

---

## 0. Hard constraints (restated — the implementer and reviewers hold these)

1. **Additive-only in components.js and app.css.** No existing function, rule, or declaration in either file is edited. components.js additions = new functions above the export literal + new keys appended inside `Planner.components = {...}` (components.js:199-207) + the new `Planner.chatInput` namespace. app.css additions = new sections appended at the end. `assets/tokens.css` is NOT owned — zero changes; no new tokens. Any value not expressible as `var(--token)` uses `calc()` over tokens or is one of the permitted non-token values: `0`, unitless ratios, percentages/viewport units, keywords, font-weights.
2. **Classic scripts only.** New JS files are IIFE + `"use strict"` like the siblings; `var`/`function` style matching components.js (no arrow functions, no template literals, no destructuring — keep the file family uniform); **no `import`/`export`/`await`** (a fence grep enforces this); promise chains only. `node --check` green on every touched JS file.
3. **No innerHTML anywhere** in the new code. Markdown rendering goes through `Planner.markdown.render` via `Planner.components.markdownBlock` only.
4. **No client-side state store.** Canonical data is refetched on every render. Transient UI state in module scope is the sanctioned pattern and is limited to exactly: `chatTranscripts`, `chatDrafts` (components.js), `skipped`, `vanished` (screens-review.js). None of these holds server-owned data (chat messages have no server-side representation at all — the server stores only `chat_session_key`, src/planner/chat/service.py:81-104).
5. **No T16/T17 scope**: no Board, no Ticket screen, no state control, no grant control (D11 #12 — the standalone picker+save on Ticket is NOT this ticket; only the accept-embedded grant-pair picker is), no create forms, no event log, no run history (D11 items 11–15).
6. **No new SPEC §15 non-goals**: no diff-style proposal views, no keyboard layer (zero `keydown` handlers — Enter-to-send explicitly not built), no instructional empty states — quiet one-liners only, no drag-and-drop, no mobile-specific layout.
7. **server.py:** the `_SHELL` string gains exactly two `<script>` lines; nothing else in the file changes. app.js itself is NOT modified.
8. **One canonical writer discipline is server-side**; the UI never computes state transitions — it only mirrors `advance_target` to know which grant options to offer (§1.1), and the server re-validates everything (machine.py:75-100).
9. Helper additions beyond the five D11 components (`quietLine`, `advanceTarget`, `gatingField`, `STATE_ORDER`, the `Planner.chatInput` registry) are utilities, not new inventory components — flagged here for orchestrator sign-off per ticket.md ("new ones need orchestrator sign-off — request via report"); this plan is that request. They exist to compose the five components without duplicating logic in two screens.
10. Gates that must stay green: `node --check` on all assets JS; `grep -nE '\b(import|export|await)\b' assets/*.js` empty; `grep -c innerHTML assets/*.js` = 1 total (markdown.js); CSS literal grep on app.css additions empty; `.venv/bin/ruff check .`; `.venv/bin/mypy src/`; `.venv/bin/pytest tests/unit -q`; smoke.py exits 0 with `SMOKE PASS`.

---

## 1. `assets/components.js` — additive block (helpers, chat-input registry, D11 items 7/8/9/10/16)

All additions go in one contiguous block between `errorLine` (ends line 197) and the `Planner.components = {...}` export literal; the export literal gains the new keys at the end of the object. New module-scope state (`chatTranscripts`, `chatDrafts`, `_atcapSeq`) sits at the top of the block.

### 1.1 Helpers (module constants + three functions, exported)

```js
// §4.1 linear order + §4.2 tables, mirrored for grant-option math only.
// The server re-validates every grant (machine.py:75-100); these never gate a write.
var STATE_ORDER = ["needs_success", "needs_approach", "needs_plan",
                   "in_progress", "needs_review", "done"];
var GATING_FIELD = { needs_success: "success", needs_approach: "approach",
                     needs_plan: "plan", in_progress: "result" };
var ADVANCE = { needs_success: "needs_approach", needs_approach: "needs_plan",
                needs_plan: "in_progress", in_progress: "needs_review" };

function advanceTarget(state, ceiling) { ... }  // -> string|null
function gatingField(state) { ... }             // -> string|null
function quietLine(text) { ... }                // -> div.quiet-line (textContent)
```

- `advanceTarget(state, ceiling)`: if `state === "in_progress" && ceiling === "done"` return `"done"`; else return `ADVANCE[state] || null`. Exact mirror of machine.py:40-48 + contracts.py:55-60 (the ceiling=done special case).
- `gatingField(state)`: `GATING_FIELD[state] || null` (contracts.py:45-50).
- `quietLine(text)`: same shape as app.js's private one (app.js:19-24); exported so screens don't near-duplicate it.
- Exports appended to the literal: `quietLine`, `advanceTarget`, `gatingField`, `STATE_ORDER` (array exposed for grant-option slicing), plus the five components below.

### 1.2 `Planner.chatInput` — the pluggable input-source registry (SPEC §14 audio seam)

Attached from components.js as a top-level namespace (like `Planner.components`):

```js
Planner.chatInput = {
  sources: { text: makeTextInputSource },   // name -> factory
  active: "text",
  register: function (name, factory, makeActive) {
    Planner.chatInput.sources[name] = factory;
    if (makeActive) { Planner.chatInput.active = name; }
  }
};
```

**Input-source contract (normative).** A source is a factory function `factory(ctx) -> HTMLElement`. The returned element is placed into the chat panel's input slot. `ctx` is:

- `ctx.submit(text)` → Promise — the ONLY way a source delivers input. The panel owns transport; the source owns how text is produced (typing today, audio transcription later). The promise settles when the round-trip completes; the source may use it to reflect busy state.
- `ctx.initialText` → string — the preserved draft to prefill (may be `""`).
- `ctx.onInput(text)` → void — the source calls this on every change to its pending text so the panel can preserve the draft across re-renders. A source with no editable pending text (audio) may never call it.

**The default typed-text source** `makeTextInputSource(ctx)` (private function, registered as `"text"`):

```
div.chat-input
  textarea.field-editor-input.chat-input-text [data-chat-input] rows=2  (value = ctx.initialText; "input" listener -> ctx.onInput)
  button.button.button--primary [data-chat-send] "Send"
```

Send click: `text = textarea.value.trim()`; if empty, return. Disable the button, clear the textarea, call `ctx.onInput("")`, then `ctx.submit(text)` and re-enable the button when the promise settles (both paths). No keydown handler (constraint 6).

**How audio drops in later** (stated so the seam is checkable): a new folder (e.g. `assets/audio/`) with one classic script that calls `Planner.chatInput.register("audio", audioFactory, true)`, plus its one `<script>` tag — no edits to chatPanel, components.js internals, or any screen. `chatPanel` reads `Planner.chatInput.sources[Planner.chatInput.active]` at render time and couples to nothing but the `ctx` contract.

### 1.3 Chat transient state (module scope, components.js)

```js
var chatTranscripts = {};  // entity_id -> [{who: "you"|"planner", text: string}]
var chatDrafts = {};       // entity_id -> string (pending, unsent input text)
```

**Why this is not a client-side state store:** the no-store rule (SPEC §9, T14 doctrine) forbids caching/reconciling **canonical server data**. Chat messages are never stored server-side — `POST /api/chat/{id}/send` returns `{reply_text, session_key}` and persists only the session key (chat/service.py:64-104). The transcript is pure conversation-render state that exists nowhere else; losing it on page reload is correct (refresh restores server state, and the server has no transcript). Module scope survives WS-flush re-renders (scripts are not re-executed; only `route()` re-runs), which is exactly the survival mechanism: every re-render rebuilds the panel DOM from `chatTranscripts[entityId]`.

### 1.4 `proposalCard(opts) -> HTMLElement` — D11 item 7

```js
function proposalCard(opts) { ... }
// opts = {
//   proposal:     {body, proposed_by, created_at},   // required
//   requireGrant: bool,                              // true => embed grantPairPicker; Accept gated on it
//   newState:     string,                            // required when requireGrant: the state the accept enters
//   onAccept:     function(payload) -> Promise       // payload = {edited_body?, next_ceiling?, at_cap?}
// }
```

DOM sketch:

```
div.proposal-card
  div.proposal-meta                        span "proposed by " + proposal.proposed_by
  markdownBlock(proposal.body)             (the proposal rendered)
  textarea.field-editor-input.proposal-edit [data-edit] rows=8   (value = proposal.body — the quick-edit prefill)
  [grantPairPicker(newState, sync)]        (only when requireGrant)
  div.proposal-card-actions
    (errorLine slot — prepended on rejection, removed on next attempt)
    button.button.button--primary [data-accept] "Accept"
```

Behavior:

- **Grant discipline** (when `requireGrant`): a local `sync()` sets `accept.disabled = inFlight || picker.getGrant() === null`. `sync` runs once at build (Accept starts disabled — nothing is picked) and is the picker's `onChange`. When `requireGrant` is false the button starts enabled (non-gating accepts carry no grant — resolution.py:174-187).
- **Edited-body rule (exact):** on Accept, `payload.edited_body` is included **iff `textarea.value !== opts.proposal.body`** (strict string comparison against the body string from this render's fetch); when equal the key is omitted entirely, so the server records a plain accept (`edited: false`, resolution.py:151-161). Whitespace-only changes count as edits — deliberate: the server stores edited text exactly (resolution.py:161), and second-guessing the human's whitespace would be cleverness.
- When `requireGrant`, `payload.next_ceiling` and `payload.at_cap` come from `picker.getGrant()` (never defaults — getGrant is null until both halves are picked, so Accept can't fire without them).
- Click: remove any prior `.error-line` in the actions row; set `inFlight = true`, `sync()`; call `opts.onAccept(payload)`; on rejection prepend `errorLine(err)` to the actions row; finally `inFlight = false`, `sync()`. No optimistic UI — success is reflected by the WS flush re-render (the accept writes events → flush → refetch).

### 1.5 `grantPairPicker(newState, onChange) -> HTMLElement` — D11 item 8

```js
var _atcapSeq = 0;
function grantPairPicker(newState, onChange) { ... }
// newState: the state the accept newly enters (wire value, e.g. "needs_approach")
// onChange: function() — called on every input change (host runs its enable/disable sync)
// Returned element carries a .getGrant property:
//   getGrant() -> {next_ceiling, at_cap} | null   (null until BOTH halves are explicit)
```

DOM sketch:

```
div.grant-picker [amber family — see §4]
  label.grant-label  span "how far"
    select.grant-ceiling [data-grant-ceiling]
      option value="" disabled selected hidden  ""        <- placeholder; value "" = not picked
      option value="none"          "No further"
      option value=<s>             <s with "_"->" ">      <- one per STATE_ORDER state with index >= index(newState)
  span.grant-atcap [data-grant-atcap]
    label  input[type=radio name="atcap-"+(++_atcapSeq) value="stop"]     span "Stop"
    label  input[type=radio name=<same>            value="propose"]       span "Propose"
```

- **Ceiling options:** `"No further"` (wire value `"none"`, the NO_FURTHER sentinel — contracts.py:86-87) plus every `STATE_ORDER` state at-or-beyond `newState` (`STATE_ORDER.slice(STATE_ORDER.indexOf(newState))`) — exactly the states `resolve_grant` accepts (machine.py:94-99, floor = the newly entered state). For `newState === "done"` (result accept on a ceiling=done ticket) the list is `No further` + `done` — the pair is still mandatory (§4.4.7 "even when the answer is nothing further").
- **Neither half defaults to picked:** the select's placeholder option is `value=""` + `disabled selected hidden` (so the control shows empty and `""` can never be re-chosen); the radios share a per-instance unique name and neither is checked. `getGrant()` returns null unless `select.value !== ""` AND one radio is checked; otherwise `{next_ceiling: select.value, at_cap: checkedRadio.value}`.
- Both the select's `change` and each radio's `change` call `onChange` — that plus `getGrant()` is the "host knows when BOTH halves are explicitly picked" surface.

### 1.6 `planTree(plan, handlers) -> HTMLElement` — D11 item 9

```js
function planTree(plan, handlers) { ... }
// plan     = {root: {focus, status}, children: [{ticket_id, note, status, position}]}  (days/api.py:75, tree.py:26-42)
// handlers = { accept(node) -> Promise, invalidate(node) -> Promise,
//              acceptAll() -> Promise,  rejectAll() -> Promise }
//   node is "root" (string) or the child position (NUMBER — the server rejects string positions, days/api.py:83-91)
```

DOM sketch:

```
div.plan-tree
  div.plan-tree-actions
    (errorLine slot)
    button.button.button--primary [data-accept-all] "Accept all"
    button.button                 [data-reject-all] "Reject all"
  div.plan-node.plan-node--root [data-node="root"] [data-status=<root.status>]
    span.plan-node-status  (root.status text)
    span.plan-node-focus   (root.focus, textContent)
    span.plan-node-actions
      button.button [data-accept] "Accept"        <- rendered ONLY when status !== "accepted"
      button.button [data-invalidate] "Invalidate"
  one div.plan-node.plan-node--child [data-node=String(position)] [data-status=<status>] per child, in array order
    span.plan-node-status  (status text)
    span.plan-node-note    (note, or the ticket_id when note is empty)
    a.plan-node-ticket [href="#/ticket/"+ticket_id] "open"   <- only when ticket_id is set
    span.plan-node-actions (same two buttons; accept passes the NUMBER position)
```

- Node status is visible twice over: the `.plan-node-status` text and the `data-status` attribute (which also drives the CSS accent — §4). `data-node` is the string form for selectors; the click closures hold the original `"root"` / number.
- Every button: disable during flight, call the handler, on rejection put `errorLine(err)` into `.plan-tree-actions`, re-enable. No local DOM mutation of statuses — the flush re-render shows the new tree (each of the four routes appends events: tree.py:84/110/123-127/150-152).

### 1.7 `chatPanel(entityId, opts) -> HTMLElement` — D11 item 10

```js
function chatPanel(entityId, opts) { ... }
// entityId: the SERVER-provided chat entity id (day.id or ticket.id) — never constructed client-side
// opts = { available: bool }   // from GET /api/chat/<entityId>/status, fetched by the host screen
```

DOM sketch:

```
div.chat-panel [data-chat-panel]
  div.chat-messages [data-chat-messages]
    per {who, text} in chatTranscripts[entityId] (or []):
      who "you"     -> div.chat-msg.chat-msg--you     [data-chat-msg="you"]      (plain textContent)
      who "planner" -> div.chat-msg.chat-msg--planner [data-chat-msg="planner"]  (contains markdownBlock(text))
  if !opts.available:
      div.chat-offline [data-chat-offline] "gateway offline"        <- the §11 notice; no input source rendered
  else:
      (errorLine slot)
      <input-source element> from Planner.chatInput.sources[Planner.chatInput.active](ctx)
```

`ctx` wiring:

- `ctx.initialText = chatDrafts[entityId] || ""`; `ctx.onInput = function (t) { chatDrafts[entityId] = t; }` — the draft survives the flush re-renders the Day screen constantly undergoes (every day action fires events).
- `ctx.submit(text)`:
  1. push `{who: "you", text: text}` onto `chatTranscripts[entityId]` (creating the array if absent); delete `chatDrafts[entityId]`; repaint the message list (rebuild `.chat-messages` children from the store — a private `paint()` closure).
  2. `return Planner.api.fetchJson("/api/chat/" + entityId + "/send", {method: "POST", body: {text: text}})` chained: on success push `{who: "planner", text: res.reply_text}` and repaint **iff the panel's root is still connected** (`root.isConnected`) — if a flush replaced the panel mid-flight, the store still has the reply and the next render shows it; on rejection prepend `errorLine(err)` above the input slot (keep the user's message in the store — it was said).
- **Transcript survival mechanism, spelled out:** WS flush → `route()` → the screen re-renders → the screen calls `chatPanel(entityId, ...)` again → the new panel rebuilds its list from module-scope `chatTranscripts[entityId]`, which the flush did not touch. The first send's reply lands *before* its own `chat_session_created` event can flush (the POST resolves first; the flush follows ≥250ms later), so the re-render always finds the completed exchange in the store. Reload clears transcripts by design (fresh JS world; the server holds no transcript).

### 1.8 `reviewCard(opts) -> HTMLElement` — D11 item 16

```js
function reviewCard(opts) { ... }
// opts = {
//   entry:  {entity_id, entity_type, kind, title, waiting_since},   // /api/queues approvals entry
//   detail: <ticket detail JSON | item detail JSON>,                // per-entry fetch by the screen
//   onSkip:          function(),                    // synchronous; screen handles re-render
//   onAccept:        function(payload) -> Promise,  // gating kinds only
//   onApprove:       function() -> Promise,         // kind "review" only
//   onAcceptStatus:  function() -> Promise          // kind "status" only
// }
```

The card's shell is `panel(entry.title, [...])` — **panel stays the only box primitive** (D11 #2); reviewCard sets its data attributes on the returned section: `[data-review-card] [data-entity-id=<entry.entity_id>] [data-kind=<entry.kind>]` plus `[data-field=<kind>]` when the kind is a gating field name.

Panel body, per kind (kinds come from the queue: gating field names / `"review"` / `"status"` — carryover.py:84-128):

- **`success` | `approach` | `plan` | `result`** (gating-field proposal; `entity_type === "ticket"`): head row (state chip via `chip("state", detail.state)`, `chip("pending-proposal")`), then `proposalCard({proposal: detail.fields[kind].proposal, requireGrant: true, newState: advanceTarget(detail.state, detail.ceiling), onAccept: opts.onAccept})`. **Guard:** if `detail.fields[kind].proposal` is null (resolved between queue fetch and detail fetch), the screen's vanish path handles it (§3) — reviewCard is never built; the screen checks before constructing.
- **`review`** (needs_review result): head row (`chip("state", detail.state)`), `markdownBlock(detail.fields.result.value)`, then — when `detail.fields.result.notes` is non-null — a `div.review-note` with `markdownBlock(notes)` (the §4.2 review notes: what needs careful checking is the substance of this approval). Action: `button.button--primary [data-approve] "Approve"`. No quick-edit textarea, no grant picker (§4.4.7: approve carries no grant; there is no proposal to edit).
- **`status`** (item status proposal; `entity_type === "item"`): head row showing `chip(null, detail.status)` + `chip(null, "→ " + detail.status_proposal.to_status)`; when `detail.status_proposal.note` is non-null, a `div.review-note` (plain text). Action: `button.button--primary [data-accept-status] "Accept"`. No grant, no textarea (item shape: sprints/views.py:30-51).

Common footer `div.review-card-actions`: (errorLine slot) + `button.button [data-skip] "Skip"` + — when `entry.entity_type === "ticket"` — `a [data-open-ticket] [href="#/ticket/"+entry.entity_id] "open ticket"` (items have no screen of their own; no link). Approve/Accept-status buttons: disable in flight, rejection → errorLine in the actions row, success → do nothing (the action's events flush → the queue refetch drops the entry → next card renders — "resolved items leave immediately").

---

## 2. `assets/screens-day.js` (new file)

Classic script, IIFE + `"use strict"`, matching siblings' style. Registers exactly one screen.

```js
(function () {
  "use strict";
  var Planner = window.Planner;
  var c = Planner.components;

  function render(root) {
    root.setAttribute("data-screen", "day");
    Promise.all([
      Planner.api.fetchJson("/api/day/today"),
      Planner.api.fetchJson("/api/queues")
    ]).then(function (results) {
      var day = results[0];
      var queues = results[1];
      return Planner.api.fetchJson("/api/chat/" + day.id + "/status").then(
        function (st) { build(root, day, queues, st.available); },
        function ()   { build(root, day, queues, false); }   // status fetch failure renders the offline notice
      );
    }).then(null, function (err) {
      root.replaceChildren(c.errorLine(err));
    });
  }

  Planner.registerScreen("day", render);
})();
```

- **Chat entity id:** `day.id` from the `/api/day/today` response IS `day_YYYY-MM-DD` (days/api.py:71-80) — used verbatim for `/api/chat/<id>/*`, never built from the browser clock. All mutating day routes derive their date segment as `day.id.slice(4)` (the server's canonical date), not `"today"`, so a boundary crossing mid-session can't retarget an in-flight action.
- `build(root, day, queues, available)` composes:

```
div.day-grid
  div.day-main
    panel("Brief", markdownBlock(day.brief))                       <- markdownBlock renders "(none)" quiet line for empty
    panel("Plan",  day.plan ? planTree(day.plan, planHandlers) : quietLine("(no plan)"))
    panel("Today", rows.length ? rows : quietLine("(none)"))
  div.day-side
    a.review-entry [data-review-entry] [href="#/review"]
      span "Review"
      span.review-entry-count [data-pending-count=String(n)]  textContent = String(n)
        (+ class "zero" when n === 0)                              <- n = queues.approvals.length
    panel("Chat", chatPanel(day.id, {available: available}))
```

- **Day ticket rows** — one per `day.tickets[i]` (already position-ordered, days/api.py:79):

```
div.day-ticket-row [data-ticket-id=<t.id>]
  entityRow({ title: t.title, href: "#/ticket/" + t.id, chips: [
      chip("priority", t.priority),
      chip("state", t.state),
      t claim_active ? chip("running-claim") : null,
      (c.gatingField(t.state) && t.fields[c.gatingField(t.state)].proposal) ? chip("pending-proposal") : null
  ]})
  button.button [data-remove] "Remove"
```

The remove button is a **sibling** of the entityRow anchor (never nested inside it — nested interactive elements would make row clicks ambiguous). Click → `DELETE /api/day/<date>/tickets/<t.id>` (remove = defer; association-only delete, days/data.py:139-162) → flush re-renders. Rejection → errorLine appended inside the "Today" panel body.

- **planHandlers** (all return the fetchJson promise; node passed as `"root"` or the number):
  - `accept(node)` → `POST /api/day/<date>/plan/accept` body `{node: node}`
  - `invalidate(node)` → `POST /api/day/<date>/plan/invalidate` body `{node: node}`
  - `acceptAll()` → `POST /api/day/<date>/plan/accept-all` (no body)
  - `rejectAll()` → `POST /api/day/<date>/plan/reject-all` (no body)

  (Routes: days/api.py:150-207; all four are human-only — `reject_agents` — and the UI sends no X-Plan-* headers, core/authctx.py:67-73.) Accept-all's server effect order adds each child ticket at the END of the day list if absent (tree.py:101-114, days/data.py:107-136) — the UI does nothing but re-render.

---

## 3. `assets/screens-review.js` (new file)

```js
(function () {
  "use strict";
  var Planner = window.Planner;
  var c = Planner.components;

  var skipped = {};   // key(entry) -> true; per-session skip memory
  var vanished = {};  // key(entry) -> true; detail-fetch-failed entries (never reset by the wrap rule)

  function key(entry) { return entry.entity_id + ":" + entry.kind; }

  function render(root) {
    root.setAttribute("data-screen", "review");
    root.classList.add("review-screen");
    Planner.api.fetchJson("/api/queues").then(function (q) {
      var entries = (q.approvals || []).filter(function (e) { return !vanished[key(e)]; });
      if (!entries.length) {
        root.replaceChildren(withAttr(c.quietLine("nothing waiting"), "data-review-empty"));
        return;
      }
      var live = entries.filter(function (e) { return !skipped[key(e)]; });
      if (!live.length) { skipped = {}; live = entries; }   // wrap rule: skipping the last entry starts over
      show(root, live[0]);
    }, function (err) { root.replaceChildren(c.errorLine(err)); });
  }

  Planner.registerScreen("review", render);
})();
```

- **Oldest-first is the server's ordering**: `/api/queues` approvals arrive sorted by `waiting_since` ascending (views.py:310, carryover.py:127) — the screen takes `live[0]`, it never re-sorts.
- **Skip memory, precisely:** `skipped` is module-scope, keyed `entity_id + ":" + kind` (an entity can legitimately reappear later under a different kind — a skip must not hide the new kind). Skip = `skipped[key] = true` + re-render (`rerender()` clears the root and calls `render(root)` again — Skip is client-only, no server event, so no flush will do it for us). Transient and per-session by construction: a reload re-executes the script, resetting `skipped = {}` — after reload the oldest entry is first again. **Wrap rule:** when every queued entry is skipped, the skip set is cleared and the walk restarts at the oldest (no dead-end, no instructional message).
- `show(root, entry)`:
  1. `path = entry.entity_type === "ticket" ? "/api/tickets/" : "/api/items/"` (routes: tickets/api.py:258-260, sprints/api.py:202-204).
  2. `fetchJson(path + entry.entity_id)` → on **rejection** (entity vanished between queue fetch and detail fetch): `vanished[key(entry)] = true; rerender();` — `vanished` is separate from `skipped` and is never cleared by the wrap rule, so a genuinely broken entry cannot cause a fetch loop; the next queue refetch drops truly-resolved entries anyway.
  3. On success, **staleness guard** before building: for gating kinds, if `detail.fields[entry.kind].proposal` is null, or `detail.state`'s gating field ≠ `entry.kind` (`c.gatingField(detail.state) !== entry.kind`); for `"review"`, if `detail.state !== "needs_review"`; for `"status"`, if `detail.status_proposal` is null — treat as vanished (same path as 2). Otherwise build the card:

```js
root.replaceChildren(c.reviewCard({
  entry: entry, detail: detail,
  onSkip: function () { skipped[key(entry)] = true; rerender(); },
  onAccept: function (payload) {
    return Planner.api.fetchJson("/api/tickets/" + entry.entity_id + "/accept/" + entry.kind,
                                 {method: "POST", body: payload});
  },
  onApprove: function () {
    return Planner.api.fetchJson("/api/tickets/" + entry.entity_id + "/approve", {method: "POST", body: {}});
  },
  onAcceptStatus: function () {
    return Planner.api.fetchJson("/api/items/" + entry.entity_id + "/accept-status", {method: "POST", body: {}});
  }
}));
```

- **Accept payload** (assembled inside proposalCard, §1.4): `{next_ceiling, at_cap}` always (from the picker — the endpoint's AcceptBody, tickets/api.py:117-121/304-322), `edited_body` iff altered per the §1.4 exact rule. Server-side floor/validation: machine.py:75-100 (`grant_missing` / `grant_invalid` errors render via errorLine).
- **Resolved items leave immediately:** every successful accept/approve/accept-status writes events → WS flush → `route()` re-runs `render` → the fresh `/api/queues` no longer lists the entry → the next-oldest card (or the empty quiet line) renders. The action buttons stay disabled from click until their card is replaced, so the ~250ms flush gap cannot double-fire.
- **Empty queue** → the single quiet line `"nothing waiting"` with `data-review-empty`. Nothing instructional.

---

## 4. `assets/app.css` — additive sections (token-only)

Appended after the existing `.quiet-line` section, in four commented blocks: `/* --- Day screen --- */`, `/* --- Plan tree --- */`, `/* --- Chat panel --- */`, `/* --- Proposal / grant / review --- */`. Every value is `var(--token)`, `calc()` over tokens, or a permitted non-token value (0, unitless ratios, %/vh, keywords, font-weights). No existing rule is edited.

**Accent discipline (D11/T14):** amber = "needs the human". In this ticket that family is: the grant-pair picker (amber wash), every primary action button (`.button--primary` — Accept, Accept-all, Approve, Send — already amber from T14), the pending-proposal chip (already amber), the review-entry count when non-zero, and `proposed` plan-node statuses (a proposed node is awaiting the human's decision). Everything else stays achromatic. Skip/Invalidate/Reject-all/Remove are plain `.button`.

Class inventory + layout intent:

| Class | Intent (all values via tokens) |
|---|---|
| `.day-grid` | `display: grid; grid-template-columns: 2fr 1fr; gap: var(--space-5); align-items: start;` — Day composes as brief→plan→list stacked in the wide main column, review-entry + chat in the narrow side column. Fixed two-column (no mobile layout, §15). |
| `.day-ticket-row` | flex row, `gap: var(--space-2)`, `align-items: center`; child `.entity-row` gets `flex: 1` via `.day-ticket-row .entity-row { flex: 1; }`. |
| `.review-entry` | block link styled like a slim panel: `display: flex; justify-content: space-between; align-items: center;` raised surface, hairline overlay border, `border-radius: var(--radius-md)`, `padding: var(--space-3) var(--space-4)`, `margin-bottom: var(--space-4)`, `text-decoration: none; color: var(--text-strong);` |
| `.review-entry-count` | pill in the accent family (mirrors `.nav-badge` values: `--accent-surface` bg, `--accent-text` color, hairline `--accent-bright` border, `--radius-pill`, `--type-xs`). |
| `.review-entry-count.zero` | achromatic override: transparent bg, `--text-faint` border/color — zero pending needs nobody. |
| `.plan-tree` | column flex, `gap: var(--space-2)`. |
| `.plan-tree-actions` | flex row, `gap: var(--space-2)`, `justify-content: flex-end`, `align-items: center`. |
| `.plan-node` | flex row, `gap: var(--space-2)`, `align-items: baseline`, `padding: var(--space-1) 0`. |
| `.plan-node--root .plan-node-focus` | `color: var(--text-strong); font-weight: 600;` |
| `.plan-node-status` | mini label: `--type-xs`, pill radius, `padding: 0 var(--space-2)`, `--surface-overlay` bg, `--text-muted`. |
| `.plan-node[data-status="proposed"] .plan-node-status` | accent family (`--accent-surface` / `--accent-text` / hairline `--accent-bright`) — awaiting the human. |
| `.plan-node[data-status="invalidated"] .plan-node-status` | `--text-faint`, transparent bg — spent. |
| `.plan-node-note` | default text. |
| `.plan-node-ticket` | `--type-sm`, `--text-muted`; hover `--text-strong`. |
| `.plan-node-actions` | `margin-left: auto; display: flex; gap: var(--space-1);` |
| `.chat-panel` | column flex, `gap: var(--space-3)`. |
| `.chat-messages` | column flex, `gap: var(--space-2)`, `max-height: 50vh`, `overflow-y: auto`. |
| `.chat-msg` | `padding: var(--space-2) var(--space-3)`, `border-radius: var(--radius-md)`, `--type-sm`, `max-width: 85%`. |
| `.chat-msg--you` | `align-self: flex-end`, `--surface-overlay` bg, `--text-strong`. |
| `.chat-msg--planner` | `align-self: flex-start`, `--surface-sunken` bg. |
| `.chat-offline` | quiet notice: `--text-muted`, `--type-sm` (achromatic — offline is absence, not a needs-human signal). |
| `.chat-input` | flex row, `gap: var(--space-2)`, `align-items: flex-end`; `.chat-input-text { flex: 1; }` (the textarea also carries `.field-editor-input` for its well styling — reuse, not duplication). |
| `select` | new base rule (additive): `font: inherit;` |
| `.grant-ceiling` | select well: `--surface-sunken` bg, `--text-default`, hairline `--text-faint` border, `--radius-sm`, `padding: var(--space-1) var(--space-2)`. |
| `.grant-picker` | the amber wash: `display: flex; gap: var(--space-4); align-items: center; flex-wrap: wrap;` `background: var(--accent-surface)`, `border-radius: var(--radius-md)`, `padding: var(--space-2) var(--space-3)`. |
| `.grant-label` | flex, `gap: var(--space-1)`, `--type-xs`, `color: var(--accent-text)`. |
| `.grant-atcap` | flex row `gap: var(--space-3)`; its labels flex with `gap: var(--space-1)`, `--type-sm`, `--accent-text`; radio inputs get `accent-color: var(--accent-bright)`. |
| `.proposal-card` | column flex, `gap: var(--space-3)`. |
| `.proposal-meta` | `--type-xs`, `--text-muted`. |
| `.proposal-card-actions` | flex, `gap: var(--space-2)`, `justify-content: flex-end`, `align-items: center`. |
| `.review-screen` | `max-width: calc(var(--space-7) * 14); margin: 0 auto;` — one centered column (~672px), the one-at-a-time surface stays a single focused card. |
| `.review-card-head` | flex row, `gap: var(--space-2)`, `align-items: center`. |
| `.review-card-actions` | flex, `gap: var(--space-2)`, `justify-content: flex-end`, `align-items: center`, `margin-top: var(--space-3)`. |
| `.review-note` | `--type-sm`, `--text-muted`. |

Audit after writing: `grep -nE '#[0-9a-fA-F]{3,8}\b|[0-9]+(px|rem|em|ms)\b|[0-9]+s\b' assets/app.css` must stay empty.

---

## 5. `src/planner/core/server.py` — the ONLY server change

In `_SHELL` (server.py:45-64), after the `app.js` line and before `</body>`, add exactly:

```html
<script src="/assets/screens-day.js"></script>
<script src="/assets/screens-review.js"></script>
```

Nothing else in server.py changes — not the assert, lifespan, routers, meta, ws, index, test router, or mount. **app.js is NOT modified**: its placeholder list already registers `day` and `review` (app.js:28-32) and overwrite-wins re-registration by later scripts is the designed mechanism (registry assignment, app.js:15-17; the screens scripts run before `DOMContentLoaded`, hence before `boot()`'s first `route()`).

---

## 6. The `data-*` attribute contract (stage-6 e2e items 24/25/28/29 select on these — exhaustive)

**Selector caveat:** T14's nav links already carry `data-screen` (components.js:53). E2E must scope screen roots as `.screen[data-screen="day"]` / `.screen[data-screen="review"]` — never bare `[data-screen=…]`.

| # | Attribute | Element | Value | Where |
|---|---|---|---|---|
| 1 | `data-screen` | the screen root div (`.screen`) | `"day"` / `"review"` | both screens |
| 2 | `data-node` | plan node row | `"root"` or child position as string (`"0"`, `"1"`, …) | planTree |
| 3 | `data-status` | plan node row | `"proposed"` / `"accepted"` / `"invalidated"` | planTree |
| 4 | `data-accept` | per-node Accept button (scope: inside `[data-node]`) AND proposalCard Accept button (scope: inside `[data-review-card]`) | none (marker) | planTree / proposalCard — two scopes, always disambiguated by ancestor |
| 5 | `data-invalidate` | per-node Invalidate button | none | planTree |
| 6 | `data-accept-all` | top Accept-all button | none | planTree |
| 7 | `data-reject-all` | top Reject-all button | none | planTree |
| 8 | `data-ticket-id` | day ticket row wrapper | the ticket id | Day list |
| 9 | `data-remove` | remove/defer button in a day row | none | Day list |
| 10 | `data-review-entry` | the Review link | none | Day side column |
| 11 | `data-pending-count` | the count pill inside the Review link | `String(approvals.length)` (also its textContent) | Day |
| 12 | `data-chat-panel` | chat panel root | none | chatPanel |
| 13 | `data-chat-messages` | message list | none | chatPanel |
| 14 | `data-chat-msg` | one message row | `"you"` / `"planner"` | chatPanel |
| 15 | `data-chat-input` | the typed source's textarea | none | text input source |
| 16 | `data-chat-send` | the typed source's Send button | none | text input source |
| 17 | `data-chat-offline` | the offline notice | none | chatPanel |
| 18 | `data-review-card` | review card root (the panel section) | none | reviewCard |
| 19 | `data-entity-id` | review card root | the entity id | reviewCard |
| 20 | `data-kind` | review card root | `"success"`/`"approach"`/`"plan"`/`"result"`/`"review"`/`"status"` | reviewCard |
| 21 | `data-field` | review card root — present for gating kinds only | the field name (equals kind) | reviewCard |
| 22 | `data-edit` | quick-edit textarea | none | proposalCard |
| 23 | `data-approve` | Approve button | none | reviewCard, kind `review` |
| 24 | `data-accept-status` | Accept button | none | reviewCard, kind `status` |
| 25 | `data-skip` | Skip button | none | reviewCard |
| 26 | `data-open-ticket` | open-ticket anchor (tickets only) | none (href carries the target) | reviewCard |
| 27 | `data-grant-ceiling` | the ceiling `<select>`; its `<option>`s carry native `value` attributes: `""` (placeholder), `"none"`, and wire state values | none on the select | grantPairPicker |
| 28 | `data-grant-atcap` | the radio-group container; the two `<input type=radio>` carry native `value="stop"` / `value="propose"` | none on the container | grantPairPicker |
| 29 | `data-review-empty` | the empty-queue quiet line | none | Review screen |

---

## 7. `orchestration/tickets/T15-day-review/smoke.py` — blueprint

Modeled line-for-line on T14's smoke.py (in-process uvicorn on a daemon thread, temp dir + DB, `PLAN_TEST_MODE=1`, `PLAN_WS_POLL_MS=50`, config via `load_config(path=None, env=...)`, `create_schema`, `build_adapters`, `create_app`, `free_or(port)`, `wait_ready`, headless chromium via `playwright.sync_api`, numbered `ok NN` prints, try/finally teardown closing client/page/browser/playwright/server and removing the temp dir, exit 0 + final `SMOKE PASS`). Run: `cd <repo> && .venv/bin/python orchestration/tickets/T15-day-review/smoke.py`.

### Setup specifics (the three clock facts that make this deterministic)

1. **`PLAN_FAKE_NOW` = the REAL current local date at T12:00:00** — `datetime.now().astimezone().date().isoformat() + "T12:00:00"`. Reason: `seed_demo` computes "today" from `datetime.now()` (demo.py:30), so fake-now must share that calendar date for `/api/day/today` (planning date of fake noon = that date, §6.1) to hit the demo day. Noon is safe at any real run time: planning date at 12:00 is always the calendar date.
2. **Seed** via `planner.seed.demo.seed_demo(conn)` on a direct connection (after `create_schema`), then **normalize seed timestamps** on the same connection: `noon = int(datetime.fromisoformat(today + "T12:00:00").astimezone().timestamp())`; `conn.execute("UPDATE tickets SET created_at = ?, updated_at = ?", (noon - 600, noon - 600))` + commit. Why: `seed_demo` stamps real wall time (demo.py:29) while the TestClock is frozen at fake noon (clock.py:27-41) — without this, t5's review `waiting_since` (the updated_at fallback: views.py:297-309; demo emits no `state_changed` events, demo.py:202-206) could land after the proposals filed at fake noon and break "t5 is oldest".
3. **Space the (d) filings with `POST /api/test/set-now`** (testmode.py:53-70): the frozen clock would otherwise give every proposal the identical `waiting_since` second, leaving the oldest-first order to an unstable tie-break (`digest.sort` on equal keys falls back to build order = `ORDER BY id` over random slugs — views.py:270-272/310, ids.py:22-27).

Resolve demo ids up front via httpx: day view (`GET /api/day/today`) → `t4, t8, t5 = tickets[0..2].id` (seed order, demo.py:247-255; identify also by title as a guard); `t1` = the single `GET /api/tickets?state=needs_success` result; the todo item = the single `GET /api/items?status=todo` result ("Research the search index options.").

All DOM waits use `page.wait_for_selector` / `page.wait_for_function` (flush counters via `window.__plannerDebug.flushes` where a re-render must be awaited) — **never bare sleeps for DOM state**.

### Checks (each one `ok NN` line)

**(a) Day renders** — `page.goto(base)"/"` → hash `#/day`; wait `.screen[data-screen="day"]`; assert: brief text "Demo day: ship the feature slice…" visible; `[data-node="root"] .plan-node-focus` = "Ship the demo feature slice."; exactly 2 `[data-node]` child rows (`"0"`, `"1"`; statuses accepted/proposed via `data-status`); 3 `[data-ticket-id]` rows in order `[t4, t8, t5]` (attribute list comparison); `[data-pending-count]` value == `"1"` (t5's review entry is the only approval); `[data-chat-panel]` present with `[data-chat-input]` and `[data-chat-send]`.

**(b) Remove + Accept-all (the "accept-all adds children to the day list" proof)** —
1. Click `[data-ticket-id="<t8>"] [data-remove]` → wait for 2 `[data-ticket-id]` rows (`wait_for_function` on `querySelectorAll` length; the removal event flushes the re-render). Order now `[t4, t5]`.
2. Click `[data-accept-all]` → wait for 3 rows; assert order exactly `[t4, t5, t8]` — t8 re-added at the END; assert all three `[data-node]` rows have `data-status="accepted"` (root + both children). Cross-check via API: `GET /api/day/today` → `plan.root.status == "accepted"`, both children `"accepted"`, `[t["id"] for t in tickets] == [t4, t5, t8]`.

**(c) Day chat echo + flush survival** — `page.fill('[data-chat-input]', "hello planner")`; click `[data-chat-send]`; wait for `[data-chat-msg="you"]` (text "hello planner") and `[data-chat-msg="planner"]` containing "echo: hello planner" (EchoGatewayAdapter, fakes.py:94-99). The first send persists a session key + logs `chat_session_created` → a flush re-renders the whole screen: wait for the flush counter to increment past its pre-send value, then assert BOTH messages are still present (module-scope transcript survived the full re-render). API: `GET /api/day/today` → `chat_session_key == "fake-sess-1"`.

**(d) Review setup via API (httpx, agent header where stated)** — all POSTs asserted 200:
1. At fake 12:00:00 — `POST /api/tickets/{t1}/propose/success` body `{"body": "Agent-drafted success criteria."}`, header `X-Plan-Actor: agent` (plain-agent classification: authctx.py:57-65; parks because t1 sits at its ceiling `needs_success` with `at_cap=propose`, demo.py:56-61).
2. `POST /api/test/set-now` `{"now": today + "T12:00:30"}` → create fresh ticket `POST /api/tickets` `{"title": "Fresh smoke ticket"}` (no headers → human; R2 default grant ceiling=needs_success/propose) → `POST /api/tickets/{fresh}/propose/success` `{"body": "Fresh proposal body."}` with the agent header (parks identically).
3. `POST /api/test/set-now` `{"now": today + "T12:01:00"}` → `POST /api/items/{item}/propose-status` `{"to": "done"}` with the agent header (sprints/api.py:234-241).
4. `GET /api/queues` → approvals length 4, order `[t5(review), t1(success), fresh(success), item(status)]` — waiting_since noon-600 < 12:00:00 < 12:00:30 < 12:01:00.

**(e) Review walk, oldest first** — `page.goto(base + "/#/review")`:
1. **Card 1 = t5**: wait `[data-review-card][data-entity-id="<t5>"]`; `data-kind="review"`; `[data-approve]` present; NO `[data-grant-ceiling]`, NO `[data-edit]` on this card; result value + review notes text rendered. **Skip**: click `[data-skip]` → wait card for t1 (skip memory advanced, no server write). **Reload**: `page.reload()` → wait card for t5 again (skip memory is per-session; reload cleared it — t5 is back first). **Approve**: click `[data-approve]` → wait card for t1 (the approval's flush refetched the queue). API: t5 `state == "done"`.
2. **Card 2 = t1** (`data-kind="success"`, `data-field="success"`): assert `[data-review-card] [data-accept]` is disabled with nothing picked; `select_option('[data-grant-ceiling]', "none")` → still disabled (one half only); `check('[data-grant-atcap] input[value="stop"]')` → enabled (both halves). Click Accept → wait card for fresh. API on t1: `state == "needs_approach"`, `ceiling == "needs_approach"` ("No further" pins the ceiling to the newly entered state, machine.py:87-89), `at_cap == "stop"`, `fields.success.value == "Agent-drafted success criteria."`.
3. **Card 3 = fresh**: assert `[data-edit]` value == `"Fresh proposal body."` (prefill); fill with `"Human-edited body. "` (note the deliberate trailing space — exactness check); this time pick the radio FIRST: `check(... input[value="propose"])` → Accept still disabled (the other single-half order); `select_option('[data-grant-ceiling]', "needs_plan")` → enabled. Click Accept → wait card for the item. API on fresh: `fields.success.value == "Human-edited body. "` **exactly** (edit-accept stores the altered text verbatim, resolution.py:161), `ceiling == "needs_plan"`, `at_cap == "propose"`, `state == "needs_approach"`.
4. **Card 4 = item** (`data-kind="status"`, no grant picker, no textarea): click `[data-accept-status]` → wait `[data-review-empty]` (queue empty → quiet line). API: item `status == "done"`, `status_proposal == null`. Assert the nav badge is hidden (`.nav-badge.hidden` present / `classList` contains `hidden`).

**(f) Offline instance** — build a SECOND config/app/uvicorn on another port with its own temp DB, same `PLAN_FAKE_NOW`, plus `PLAN_GATEWAY_ADAPTER="offline"` (registry.py:61-69 → `OfflineGatewayAdapter`, fakes.py:102-108); `create_schema` + `seed_demo` + the same timestamp normalization; new browser page → `#/day`: wait `[data-chat-offline]`; assert brief text renders and 3 `[data-ticket-id]` rows exist (rest of the screen intact); assert NO `[data-chat-send]` (no input source when offline). Teardown stops both servers.

---

## 8. Risks and edge cases — each with its decided handling

1. **Mid-interaction WS flush wipes picker/textarea state (Review).** Any unrelated event (dispatcher tick, another agent's proposal) flushes → full re-render → the grant picker resets and the quick-edit re-prefills. **Decision: acceptable v1.** Rationale: the re-render-everything doctrine is T14 law; preserving per-card edit state would need a keyed store invalidated on proposal supersession — machinery §15's spirit rejects for v1. The review surface is one-at-a-time with short dwell; the human's own actions flush only *after* the card resolves. Recorded here so stage-6 flake triage starts from "known, accepted".
2. **Mid-typing flush wipes the day chat draft.** This one is NOT accepted — the Day screen self-flushes on every action, so the draft dies constantly without help. **Decision: mitigated** via `chatDrafts` (module scope; §1.7): the typed source reports every change (`ctx.onInput`), the rebuilt panel prefills from the store. Transient UI state, sanctioned pattern.
3. **Result-field accepts where ceiling=done route to done.** `advanceTarget("in_progress", "done") === "done"` — the picker then offers exactly `No further` + `done`, matching the server floor (`resolve_grant(done, …)`, machine.py:94-99); the pair remains mandatory (§4.4.7). Covered by the helper mirror (§1.1); no special-case UI.
4. **Entity vanished between queue fetch and detail fetch.** Detail rejection → mark in `vanished` (never wrap-reset) → re-render refetches the queue. No loop: `vanished` filters the entry even if a stale queue briefly re-lists it; genuinely resolved entries drop from the next queue payload (the approvals view derives from live rows, views.py:269-325). Same path handles the staleness guard (proposal superseded-to-resolved / state moved on) in §3.3.
5. **Day with `plan: null` vs plan with zero children.** `null` → the Plan panel renders `quietLine("(no plan)")` (reject-all sets plan NULL, tree.py:147-153 — this is the normal post-reject state). A tree with zero children renders the root row + top actions only (accept-all on zero children is a valid server call, tree.py:101-114). Both explicitly built, neither instructional.
6. **Chat entity id for a day.** Always `day.id` from the `/api/day/today` response (`day_YYYY-MM-DD`, days/api.py:71-80) — never constructed from the browser clock (the browser knows nothing about the 05:00 boundary rule). Mutating day routes reuse `day.id.slice(4)` for the same reason (§2).
7. **Reply lands while the panel is detached** (flush replaced the screen mid-send). The reply is appended to the module store unconditionally; repaint is guarded by `root.isConnected`. The message appears on the next render (next flush or navigation). No lost data, no detached-DOM writes.
8. **Double-fire in the flush gap.** Every mutating button disables itself from click until its promise settles AND the re-render replaces it; the ~250ms debounce window cannot re-submit.
9. **`data-screen` collision with nav links** (components.js:53). Contract states the scoped selector (`.screen[data-screen=…]`); smoke uses it, stage-6 must too (§6 caveat row).
10. **Frozen-clock ordering in smoke** (three interacting facts): seed stamps real time, TestClock is frozen, approvals tie-break is unstable at equal seconds. Handled in §7 setup (normalization + set-now spacing) — the plan's ordering assertions are exact, not approximate.

---

## 9. What I verified in the codebase (file:line)

**Foundation (law):**
- `Planner.registerScreen(name, render)` + overwrite-wins + placeholders for day/review — assets/app.js:15-17, 26-32.
- Every WS flush re-runs `route()` (full re-render, fresh fetches) and refreshes the badge — assets/app.js:65-79; badge = approvals length — app.js:66-73.
- Route shapes / screen root creation (`div.screen`, render(root, params)) — assets/app.js:34-63.
- Primitives + style rules (`make`/`appendChildren`, textContent-only; exports literal to extend) — assets/components.js:9-33, 199-207; `panel` is the only box primitive — components.js:89-97; `chip` variants incl. `pending-proposal`/`running-claim` — components.js:142-177; `entityRow` is a bare anchor — components.js:180-189; `errorLine(err)` consumes `{code, message}` — components.js:192-197.
- `fetchJson` rejection carries `{code, message, detail, status}` — assets/api.js:26-33, 37-78.
- The sole innerHTML is markdown.js — assets/markdown.js:175-178; components.js header confirms — components.js:1-4.
- Nav links already use `data-screen` — components.js:53.
- `.button--primary` and accent chips carry the amber family — assets/app.css:239-243, 267-271, 320-324; tokens closed set — assets/tokens.css:7-58.
- `_SHELL` current script list (insertion point) — src/planner/core/server.py:45-64.

**Day + plan tree:**
- `GET /api/day/{date}` (`today` resolved via planning date) → `{id, brief, notes, plan|null, chat_session_key, created_at, updated_at, tickets:[ticket_json in position order]}` — src/planner/days/api.py:52-62, 65-80, 108-111.
- `plan` dict shape `{root:{focus,status}, children:[{ticket_id,note,status,position}]}` — src/planner/days/logic/tree.py:26-42.
- Plan routes accept/accept-all/invalidate/reject-all, human-only, child node must be an int — days/api.py:150-207, 83-91.
- Accept-all accepts root+children and adds child tickets at the END if absent (idempotent) — tree.py:101-114; days/data.py:107-136 (position = current count).
- `DELETE /api/day/{date}/tickets/{ticket_id}` = association-only delete + contiguous re-pack — days/api.py:140-147; days/data.py:139-162.
- `POST /api/day/{date}/tickets` body `{ticket_id}` — days/api.py:129-137.

**Queues + review:**
- `GET /api/queues` → `{approvals, pickup, overdue}`; approvals entries `{entity_id, entity_type: "ticket"|"item", kind, title, waiting_since}` sorted oldest-first — src/planner/tickets/api.py:452-458; src/planner/tickets/views.py:265-325 (sort :310); kinds = gating field name | "review" | "status" — src/planner/days/logic/carryover.py:84-128; review `waiting_since` = last `state_changed→needs_review` event, else updated_at fallback — views.py:297-309.
- Ticket detail: `state`, `ceiling`, `at_cap`, `fields.{success,approach,plan,result}` each `{value, proposal:{body,proposed_by,created_at}|null, notes}` — tickets/views.py:38-62, 121-160; contracts.py:63-83.
- `POST /api/tickets/{id}/accept/{field}` body `{edited_body?, next_ceiling?, at_cap?}`, human-only — tickets/api.py:117-121, 304-322; grant pair mandatory (`grant_missing`), floor at-or-beyond the newly entered state, `"none"` sentinel pins to the new state (`grant_invalid` otherwise) — src/planner/tickets/logic/machine.py:75-100; wire sentinel `NO_FURTHER = "none"` — src/planner/tickets/contracts.py:86-87; STATE_ORDER — contracts.py:26-29; ADVANCE_TARGET + ceiling=done special case — contracts.py:55-60, machine.py:40-48.
- Edit-accept: `edited` flag = `edited_body is not None`; stored verbatim; new state computed from the CURRENT ceiling; non-gating accepts need no grant — src/planner/tickets/logic/resolution.py:142-187.
- `POST /api/tickets/{id}/approve` — human-only, no grant, needs_review→done — tickets/api.py:325-330; resolution.py:190-199.
- `GET /api/items/{item_id}` → `status`, `status_proposal:{to_status,note,proposed_by,created_at}|null` — src/planner/sprints/api.py:202-204; src/planner/sprints/views.py:30-51.
- `POST /api/items/{item_id}/accept-status` — human-only, no grant — sprints/api.py:244-248; `propose-status` — sprints/api.py:234-241.
- Human requests carry no X-Plan-* headers; `X-Plan-Actor` alone = plain agent; `reject_agents` guards every accept/approve/plan route — src/planner/core/authctx.py:22-27, 46-83, 184-192.
- Error envelope `{error:{code,message,detail}}` + codes (`grant_missing`, `grant_invalid`, `gateway_offline`, `not_found`, …) — src/planner/core/errors.py:12-40.

**Chat:**
- `POST /api/chat/{entity_id}/send` `{text}` → `{reply_text, session_key}`; `GET /api/chat/{entity_id}/status` → `{available}` — src/planner/chat/api.py:20-43.
- Day entity = `day_YYYY-MM-DD` (canonical check + materialize-on-read); session key persisted on first reply only; that's ALL the server stores about a conversation — src/planner/chat/service.py:37-61, 64-104.
- Echo fake mints `fake-sess-1` and replies `"echo: " + text`; offline fake: `status().available == False`, send raises `gateway_offline` — src/planner/core/adapters/fakes.py:86-108; `"offline"` gateway selectable via config — src/planner/core/adapters/registry.py:61-69; env key `PLAN_GATEWAY_ADAPTER` — src/planner/core/config.py:164.

**Smoke substrate:**
- T14 smoke harness pattern (uvicorn thread, env, ports, waits, teardown) — orchestration/tickets/T14-ui-foundation/smoke.py:85-220.
- `seed_demo` requires empty DB, uses REAL `time.time()` / `datetime.now()` for "today" — src/planner/seed/demo.py:23-37 (:29-30); demo day = today with plan (root accepted; children t4 accepted / t8 proposed) and day tickets t4,t8,t5 at positions 0,1,2 — demo.py:221-257; t1 needs_success at ceiling needs_success/propose — demo.py:56-61; t5 needs_review, ceiling done, review notes set — demo.py:82-90; todo item title — demo.py:46-48; demo emits only *_created events (no state_changed) — demo.py:130-135, 156-160, 202-206, 246, 252-256.
- TestClock is frozen (mutable only via set-now) — src/planner/core/clock.py:27-41, 51-54; `POST /api/test/set-now` `{now}` — src/planner/core/testmode.py:53-70; `PLAN_FAKE_NOW` honored only in test mode — config.py:131-133.
- Entity ids are random slugs (no creation-order in `ORDER BY id`) — src/planner/core/ids.py:10-27.

---

## 10. Execution order + self-checks (implementer runs fresh, in order)

1. components.js additive block (§1) → `node --check assets/components.js`.
2. screens-day.js (§2), screens-review.js (§3) → `node --check` each.
3. app.css additions (§4) → literal grep (§4 tail) empty.
4. server.py `_SHELL` two lines (§5) → `git diff src/planner/core/server.py` shows exactly two added lines.
5. Fences: `grep -nE '\b(import|export|await)\b' assets/*.js` empty; `grep -c innerHTML assets/*.js` totals 1 (markdown.js).
6. `.venv/bin/ruff check .` · `.venv/bin/mypy src/` · `.venv/bin/pytest tests/unit -q` — all green (no Python behavior changed; smoke.py must itself be ruff-clean).
7. smoke.py (§7) → full output ends `SMOKE PASS`, exit 0.

The orchestrator's gate remains `./verify` (unchanged scoreboard expectations at this stage) plus this smoke; stage-6 items 24/25/28/29 will drive these screens through the §6 contract.

## 11. Open questions

None. Delegated calls made and recorded inline: helper additions flagged for sign-off (§0.9); Accept hidden on already-accepted plan nodes (§1.6); review notes shown on review-kind cards (§1.8, grounded in §4.2); chat draft preserved / review-card edit loss accepted (§8.1–8.2); skip wrap rule (§3); exact-string edited_body rule including whitespace (§1.4).

---

## 12. BINDING AMENDMENTS (post codex plan review — these override the sections above where they conflict; see plan-review.md for dispositions)

**A1 — no `withAttr` helper.** §3's empty-queue line is built inline in screens-review.js:

```js
var empty = c.quietLine("nothing waiting");
empty.setAttribute("data-review-empty", "");
root.replaceChildren(empty);
```

No new exported helper; the §1.1 helper list is final (`quietLine`, `advanceTarget`, `gatingField`, `STATE_ORDER`).

**A2 — `vanished` is DELETED.** screens-review.js keeps exactly ONE module-scope transient set: `skipped` (human skips + auto-skips; reload resets it; the wrap rule clears it when every entry is skipped). The two failure paths become:

- **Detail-fetch rejection** (transient or otherwise): do NOT hide the entry and do NOT auto-refetch. Render an inline error surface in the screen root: `errorLine(err)`, a quiet line with `entry.title`, and a `button.button [data-skip] "Skip"` whose click does `skipped[key(entry)] = true; rerender();` — plus, for tickets, the `[data-open-ticket]` anchor. The human decides to move on; nothing is hidden for the session; no fetch loop is possible (nothing refetches until the human acts or a flush lands).
- **Staleness guard** (detail fetched fine but the entry no longer matches: gating proposal null / gating field mismatch / state left needs_review / status_proposal null): if `skipped[key(entry)]` is NOT yet set — `skipped[key(entry)] = true; rerender();` (the fresh `/api/queues` in that rerender will almost always have dropped the entry). If it IS already set (i.e., the wrap rule re-surfaced an entry that is stale again — the only way a loop could start): render the A1 empty quiet line and stop. Bounded, transient, nothing permanently hidden — a reload or the next flush re-derives everything from the server.

§8.4's mechanism text is superseded by this; its goal (no loop, no lost entries) stands.

**A3 — stay disabled on success.** For every mutating control this ticket builds (proposalCard Accept, reviewCard Approve / Accept-status, planTree per-node Accept/Invalidate and Accept-all/Reject-all, the day row Remove, the chat Send is exempt — chat has no flush-replacement contract and already re-enables on settle by design §1.2): on click → disable (and clear any prior `.error-line`); on rejection → render `errorLine(err)` and re-enable (human corrects and retries); on success → **stay disabled** — the WS-flush re-render replaces the DOM, so re-enabling would only open the §8.8 double-fire gap. In proposalCard terms: `sync()` disables when `inFlight || resolved || (requireGrant && picker.getGrant() === null)`, where `resolved` latches true on fulfilled accept. §1.4's "finally inFlight = false" and §8.8's claim are superseded accordingly. T14's fieldEditor keeps its own settle-re-enable behavior — the foundation is not reworked.

**A4 — innerHTML fence, corrected.** The self-check is: `grep -n 'innerHTML' assets/screens-day.js assets/screens-review.js` → no output, and the components.js additive block plus the app.css additions contain zero `innerHTML` occurrences (check the diff). No repo-wide count (markdown.js legitimately matches, comments included). §0.10 and §10.5 read accordingly.

**A5 — smoke goto typo.** `page.goto(base + "/")` (T14 smoke.py:151 pattern). Applies wherever §7 navigates.

**A6 — plan-node count selectors.** Smoke child-row assertions use `.plan-node--child` (expect 2 in the demo); the root row is `[data-node="root"]`; a bare `[data-node]` matches 3 and is only ever asserted as 3. §7(a)'s "exactly 2 `[data-node]` child rows" reads "exactly 2 `.plan-node--child` rows".
