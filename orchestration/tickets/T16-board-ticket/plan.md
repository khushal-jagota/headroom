# T16 — Board + Ticket screens — implementation plan

Stage 5, on the T14 foundation. Deliverable is the two screens plus four additive
components. Implementer builds against contracts and reviewed API truth only —
invents no shapes, modifies no contracts, touches no file outside the list below.

## 0. Scope, files, and standing assumptions

**Files the implementation MAY write (nothing else):**

| File | New/edit | What |
|---|---|---|
| `assets/screens-board.js` | NEW | classic IIFE; `Planner.registerScreen("board", render)` |
| `assets/screens-ticket.js` | NEW | classic IIFE; `Planner.registerScreen("ticket", render)` |
| `assets/components.js` | ADDITIVE ONLY | add exactly four functions to `Planner.components`: `stateControl`, `grantControl`, `eventLog`, `runHistory`, plus private helpers inside the existing IIFE |
| `assets/app.css` | ADDITIVE ONLY | append new sections; every value tokens-only |
| `orchestration/tickets/T16-board-ticket/smoke.py` | NEW | self-smoke, modelled on T14's |

**Coding style (binding, from T14):** classic IIFE `(function(){ "use strict"; var Planner = window.Planner … })()`; DOM built with `document.createElement` / `textContent` / `setAttribute` only — **no `innerHTML`** (the only `innerHTML` in the repo is `markdown.js`). Screens do not import `make`/`appendChildren` (those are private to components.js); they call `document.createElement` directly and compose `Planner.components.*`. No client-side state store, no `navigate()` — anchors carry state in the URL.

**Rendering model (binding, from T14):** `render(root, params)` rebuilds the whole screen from fresh fetches. After a **successful** mutating action the code does **not** re-render — the WS invalidation bus debounce-flushes, `app.js onFlush()` re-runs `route()`, and `route()` calls the screen's `render` again on a fresh `screenDiv`. Buttons disable while a request is in flight and re-enable after (mirror `fieldEditor` in components.js lines 111–138). A failed action renders `Planner.components.errorLine(err)` inline near the control that failed; it does **not** replace the screen.

**Server-shell script tags = orchestrator glue (out of scope here).** `_SHELL` in `src/planner/core/server.py` (lines 57–61) currently lists five script tags ending at `/assets/app.js`. The orchestrator adds `<script src="/assets/screens-board.js"></script>` and `<script src="/assets/screens-ticket.js"></script>` **after** `/assets/app.js` (order matters: `Planner.registerScreen` and `Planner.components` must exist first; the screens overwrite the app.js placeholders — overwrite-wins, per app.js lines 27–32). The smoke assumes those two tags are present. This plan does not edit server.py.

**T15 dependency (binding).** `screens-ticket.js` composes two components that **T15 owns and has not yet landed**: `Planner.components.proposalCard` (hosts the grant-pair picker) and `Planner.components.chatPanel`. T16 **reuses, never redefines** them. Their exact call shapes are treated as EXTERNAL CONTRACTS — see §7. The ticket screen cannot render, and the smoke cannot pass, until T15 is integrated. The implementer writes to the assumed signatures in §7 and the integrator reconciles them mechanically against T15's landed code.

**JS state constant.** There is no shared JS enum. `components.js` gets one private const inside its IIFE:
`var STATE_ORDER = ["needs_success","needs_approach","needs_plan","in_progress","needs_review","done"];`
used by `stateControl` and `grantControl` for their selects. The board never needs it (server serves columns already in order); the ticket screen never needs it (the controls own their selects). Link kinds `["belongs_to","parent_child","blocks","relates"]` (§3.6) live as a private const in `screens-ticket.js`.

---

## 1. `assets/components.js` — four additive components + private helpers

Append inside the existing IIFE (before the `Planner.components = {…}` export; extend that object literal with the four names). No existing function is edited.

### 1.1 Private helpers (not exported)

- `var STATE_ORDER = [ …six values… ];` (above).
- `function stateLabel(s){ return String(s).replace(/_/g, " "); }` — display form for state option labels/text (matches chip("state") behaviour, components.js line 160).
- `function formatUnix(seconds){ if (seconds === null || seconds === undefined) return ""; return new Date(Number(seconds) * 1000).toLocaleString(); }` — the one shared time formatter for `runHistory` and `eventLog`. (Exact string is not asserted by the smoke; only that rows render.)
- `function eventSummary(kind, payload){ … }` — the exact formatter (constraint 5), payload keys confirmed against `resolution.py`/`api.py` writers:
  - `"state_changed"` → `String(payload.from) + " → " + String(payload.to)` (payload `{from,to,cause}`, §4.4.6)
  - `"proposal_accepted"` → `String(payload.field) + " · " + String(payload.resolved_by)` (payload `{field,body,resolved_by,edited}`)
  - `"ticket_updated"` → `String(payload.field)` (payload `{field,from,to}`)
  - default → `""` (everything else, incl. `ticket_created`, `proposal_superseded`, `link_added`, etc. — kind + time still show; summary empty)
- `function inFlight(button, errorHost, fn){ … }` — the disable→run→errorLine→re-enable pattern for the two components that carry buttons (`stateControl`, `grantControl`). Same shape as `fieldEditor`: clear any prior `.error-line` in `errorHost`, `button.disabled = true`, `Promise.resolve(fn()).then(noop, function(err){ errorHost.prepend(errorLine(err)); }).then(function(){ button.disabled = false; })`.

### 1.2 `stateControl(opts)` → DOM node  (D11 #11; §10.4 "state control")

`opts = { state: string, autoBlocked: boolean, onJump: function(toState) → Promise, onDrop: function() → Promise, onUnblock: function() → Promise }`

DOM sketch:
```
div.state-control            [data-state-control]
  span.state-control-current  → text stateLabel(opts.state)
  select.state-control-select [data-state-select]     // one <option value=s> per STATE_ORDER, label stateLabel(s), opts.state preselected
  button.button [data-state-jump]   "Jump"
  button.button [data-drop]         "Drop"
  button.button [data-unblock]      "Unblock"          // rendered ONLY when opts.autoBlocked
  div.state-control-error                              // errorHost for inFlight
```
Behaviour:
- Jump → `inFlight(jumpBtn, errorHost, () => opts.onJump(selectEl.value))`.
- Drop → `inFlight(dropBtn, errorHost, () => opts.onDrop())`.
- Unblock (only if `autoBlocked`) → `inFlight(unblockBtn, errorHost, () => opts.onUnblock())`.
- No re-render on success (WS flush handles it).

### 1.3 `grantControl(opts)` → DOM node  (D11 #12; §10.4 "ceiling and at-cap as plain pickers (no dial widgetry)")

`opts = { ceiling: string, atCap: "stop"|"propose", onSave: function(ceiling, atCap) → Promise }`

DOM sketch:
```
div.grant-control            [data-grant-control]
  select.grant-control-ceiling [data-grant-ceiling]   // <option> per STATE_ORDER, opts.ceiling preselected
  div.grant-control-atcap      [data-grant-at-cap]     // two <label><input type=radio name=<unique>> — "stop" / "propose", opts.atCap checked
  button.button [data-grant-save]  "Save"
  div.grant-control-error
```
Behaviour: Save → `inFlight(saveBtn, errorHost, () => opts.onSave(ceilingSel.value, checkedRadioValue()))`. Radio group `name` must be unique per instance (e.g. derive from a module counter) so multiple grant controls never collide. No dial/slider — plain select + radios only.

### 1.4 `eventLog(events)` → DOM node  (D11 #14; §10.4 "event log")

`events` = array as served by `GET /api/tickets/{id}/events` → `{events:[{id,entity_id,kind,payload,created_at}]}` (ascending `id`, render in given order).

DOM sketch:
```
div.event-log                [data-event-log]
  // per event, in order:
  div.event-log-row           [data-event-row][data-event-kind=<kind>]
    span.event-log-kind   → kind
    span.event-log-summary → eventSummary(kind, payload)
    span.event-log-time   → formatUnix(created_at)
  // empty → div.quiet-line "(no events)"
```

### 1.5 `runHistory(runs)` → DOM node  (D11 #15; §10.4 "run history")

`runs` = array as served by `GET /api/tickets/{id}/runs` → `{runs:[{id,ticket_id,status,started_at,ended_at,summary,error,pid}]}` (newest first — `started_at DESC, id DESC`; render in given order).

DOM sketch:
```
div.run-history              [data-run-history]
  // per run, in order:
  div.run-history-row         [data-run-row][data-run-status=<status>]
    span.run-history-status  → status
    span.run-history-times   → formatUnix(started_at) + (ended_at ? " – " + formatUnix(ended_at) : "")
    span.run-history-detail  → summary || error || ""
  // empty → div.quiet-line "(no runs)"
```

### 1.6 Export

Extend the export object (components.js lines 199–207) with `stateControl, grantControl, eventLog, runHistory`. Existing entries untouched.

---

## 2. `assets/screens-board.js`  (§10.3 Board)

Classic IIFE. `var Planner = window.Planner; var config = Planner.config; var C = Planner.components; var api = Planner.api;` then `Planner.registerScreen("board", render);`.

### 2.1 `render(root, params)`

- Fetch `api.fetchJson("/api/board")` → `{columns:[{state, cards:[{id,title,priority,deadline,project,has_pending_proposal,has_running_claim}]}]}`. (`board_view`, tickets/views.py 224–259: exactly six columns in `STATE_ORDER`, `dropped` never present, cards pre-sorted server-side.)
- On rejection: `root.appendChild(C.errorLine(err))`; return.
- On success: build the screen:
```
section.board-screen         [data-screen="board"]
  div.board                                              // horizontal scroll container
    // per column, in served order:
    div.board-column          [data-column=<col.state>]
      div.board-column-head   → stateLabel(col.state) + " " + col.cards.length   // name + count
      div.board-column-cards
        // per card → cardAnchor(card)   (below)
        // if col.cards empty → div.quiet-line "(empty)"
```
- Append `section` to `root`.

### 2.2 `cardAnchor(card)` → DOM node

Reuse `C.entityRow` then decorate (entityRow does not set `data-*`):
```
var chips = [
  C.chip("priority", card.priority),
  card.deadline ? C.chip("deadline", card.deadline) : null,   // plain; NO overdue flag (server sends none; no client date math)
  card.project  ? C.chip("project",  card.project)  : null,
  card.has_pending_proposal ? marker("pending-proposal") : null,
  card.has_running_claim    ? marker("running-claim")    : null
];
var a = C.entityRow({ href: config.ROUTES.ticketPrefix + card.id, title: card.title, chips: chips });
a.setAttribute("data-card", "");
a.setAttribute("data-ticket-id", card.id);
return a;
```
`marker(v)` = `var c = C.chip(v); c.setAttribute("data-marker", v); return c;` — reuses the existing chip marker variants (`chip--pending-proposal` "proposal pending", `chip--running-claim` "● running", components.js 142–148/170–172). No drag-and-drop; the whole card is the click-through anchor (`entityRow` is a native `<a>`).

---

## 3. `assets/screens-ticket.js`  (§10.4 Ticket)

Classic IIFE, same header aliases. `Planner.registerScreen("ticket", render);`. Private consts: `LINK_KINDS = ["belongs_to","parent_child","blocks","relates"];`. Private helpers listed in §3.3.

### 3.1 `render(root, params)`

`var id = params.id;`

Parallel fetch (single Promise.all so one failure is one error, and the four are independent):
```
Promise.all([
  api.fetchJson("/api/tickets/" + id),          // detail  (ticket_detail)
  api.fetchJson("/api/tickets/" + id + "/events"),
  api.fetchJson("/api/tickets/" + id + "/runs"),
  api.fetchJson("/api/sprints")                 // {sprints:[{id,name,…}]}
]).then(onLoaded, function(err){ root.appendChild(C.errorLine(err)); });
```
`not_found` on the detail rejects the whole Promise.all → single `errorLine`, no crash (constraint 5). `onLoaded([detail, evs, runs, sprints])` builds the screen root and appends the panels in the order below, then appends to `root`.

`detail` shape (ticket_detail, tickets/views.py 121–160): the full `ticket_json` (id,title,state,priority,deadline,project,sprint_item_id,sprint_id,recap,ceiling,at_cap,auto_blocked,consecutive_failures,chat_session_key,alias,fields,claim_expires,claim_active,created_at,updated_at) **plus** `blocked`, `effective_sprint_id`, `links[]`, `day_ids[]`, `run_summary`. `fields` is `{success,approach,plan,result}` each `{value,proposal,notes}`.

Screen root:
```
section.ticket-screen   [data-screen="ticket"][data-ticket-id=<id>][data-state=<detail.state>]
```

### 3.2 Sections, in order

**(1) Header** — `C.panel(detail.title, [chipsRow])` (or a bespoke `ticket-header` block titled by the ticket title). Chips row:
- `chip("state", detail.state)`, `chip("priority", detail.priority)`, `detail.project && chip("project", detail.project)`, `detail.deadline && chip("deadline", detail.deadline)`.
- Markers (each `chip(...)` with `data-marker` set):
  - `detail.claim_active` → `marker("running-claim")`.
  - `detail.auto_blocked` → `marker("auto-blocked")` (chip variant `auto-blocked`, "auto-blocked").
  - `detail.blocked` (link-blocked) → `var b = chip("blocked", "blocked"); b.setAttribute("data-marker","blocked");` — `"blocked"` is not a known chip variant, so it hits the default branch (components.js 173–174): a plain `.chip` with text "blocked". (No existing marker variant means "link-blocked"; the default chip is the closest, per constraint 5.)
- Header markers reuse `[data-marker=…]` (constraint 6). No pending-proposal chip in the header — the pending proposal lives in its field section.

**(2)–(5) Field sections** success / approach / plan / result — one `C.panel(fieldName, body)` each, panel body element carries `[data-field=<name>]`:
- `C.markdownBlock(detail.fields[name].value)` — value as markdown (§10.4 "value rendered as markdown"; §4.2).
- If `detail.fields[name].proposal` is non-null → append `C.proposalCard({…})` (T15, §7). ALL non-null proposals render, not just the gating one; Accept on a non-gating field calls the same endpoint and the server decides (constraint 5; resolution.py `decide_accept` sets value only for non-gating, requires the grant pair only on the gating branch, lines 162–174). onAccept → `POST /api/tickets/{id}/accept/{name}` body `{edited_body, next_ceiling, at_cap}` (AcceptBody, tickets/api.py 117–121, 304–322; `next_ceiling` a TicketState value or `"none"`, `at_cap` `"stop"|"propose"`).
- Notes → `C.fieldEditor(detail.fields[name].notes, function(note){ return api.fetchJson("/api/tickets/"+id+"/notes/"+name, {method:"PUT", body:{note:note}}); })` (§4.2 notes; PUT /notes/{field}, NoteBody `{note}`, tickets/api.py 333–343).

**(6) Recap** — `C.panel("Recap", [...])`, body `[data-recap]`: `C.markdownBlock(detail.recap)` + `C.fieldEditor(detail.recap, function(body){ return api.fetchJson("/api/tickets/"+id+"/recap", {method:"PUT", body:{body:body}}); })` (§10.4 recap; PUT /recap, RecapBody `{body}`).

**(7) State control** — `C.panel("State", [ C.stateControl({ state: detail.state, autoBlocked: detail.auto_blocked, onJump, onDrop, onUnblock }) ])`:
- `onJump = function(to){ return api.fetchJson("/api/tickets/"+id+"/state", {method:"POST", body:{to:to}}); }` (POST /state, StateBody `{to}`, human-only).
- `onDrop = function(){ return api.fetchJson("/api/tickets/"+id+"/drop", {method:"POST"}); }` (POST /drop).
- `onUnblock = function(){ return api.fetchJson("/api/tickets/"+id+"/unblock", {method:"POST"}); }` (POST /unblock; clears `auto_blocked`, tickets/api.py 403–409). Unblock button only renders when `detail.auto_blocked` (component handles it).

**(8) Grant control** — `C.panel("Grant", [ C.grantControl({ ceiling: detail.ceiling, atCap: detail.at_cap, onSave: function(ceiling, atCap){ return api.fetchJson("/api/tickets/"+id+"/grant", {method:"POST", body:{ceiling:ceiling, at_cap:atCap}}); } }) ])` (§4.3; POST /grant, GrantBody `{ceiling, at_cap}` — both required, server errors `grant_missing` if either absent, tickets/api.py 356–382).

**(9) Copy ticket** — `C.panel("Copy", [copyButton])`. `copyButton` = `<button class="button" data-copy>` "Copy ticket". On click, in-flight:
```
fetchText("/api/tickets/"+id+"/copy-text")          // §7.6? no — plain text; MUST NOT use fetchJson (JSON.parse would reject text)
  .then(copyToClipboard)                             // clipboard write
  .then(function(){ flash button text → "Copied", restore after a short timeout },
        function(err){ errorHost.prepend(C.errorLine(err)); })
```
`copy-text` returns `text/plain` (`PlainTextResponse`, tickets/api.py 424–426 → `copy_text`, views.py 184–218: title / state / priority / success / approach / plan / result / recap / links block). This is a read, not a WS-mutating action; no flush follows, so the "Copied" flash is the only feedback and is set directly (the sole sanctioned direct DOM tweak in the ticket screen; documented as such).

**(10) Links** — `C.panel("Links", [linksList, addForm])`. `linksList` `[data-links]`:
- Per `detail.links` entry `{from_id,to_id,kind}` → a row `[data-link-row][data-link-kind=<kind>]`:
  - text: `kind` · `from_id` " → " far-end. Far end = the id that is not `id`; if it starts with `"t_"` render it as `<a href="#/ticket/"+farId>` (both `from_id` and `to_id` shown; only ticket ids get anchors — sprint-item/other ids render as plain text).
  - Remove button → `DELETE /api/links?from_id=<>&to_id=<>&kind=<>` (query params, remove_link, tickets/api.py 438–444). Build the query with `encodeURIComponent`.
- `addForm` `[data-link-add]`: `from_id` text input (prefilled with `id`), `to_id` text input, `kind` `<select>` over `LINK_KINDS`, Add button → `POST /api/links` body `{from_id, to_id, kind}` (add_link, tickets/api.py 429–435).

**(11) Day / sprint assignment** —
- Day `[data-day-assign]`: date `<input type="date">` defaulted to the client's today (`new Date().toISOString().slice(0,10)`) + "Add to day" button → `POST /api/day/{isoDate}/tickets` body `{ticket_id: id}` (days/api.py 129–137; `{date}` accepts ISO or `today`; client sends ISO). Existing `detail.day_ids` each a row `[data-day-row]` showing the day id + Remove → `DELETE /api/day/{date}/tickets/{id}` where **`date = dayId.slice("day_".length)`** i.e. `dayId.slice(4)` (ids.py 30–31: `day_id` = `"day_" + isoDate`; e.g. `day_2026-07-05` → `2026-07-05`).
- Sprint `[data-sprint-assign]`: if `detail.sprint_item_id != null` → plain text `"sprint: " + (detail.effective_sprint_id || "(none)")`, **no select** (server rejects `set_sprint` on parented tickets — `_set_project`/`set_sprint` guard, and sprint is derived through the item). Else → `<select>` over `sprints.sprints` (option value = `s.id`, label = `s.name`) plus a leading `"(none)"` option (value `""`), preselected to `detail.sprint_id`; Save → `PATCH /api/tickets/{id}` body `{sprint_id: value || null}` (patch_ticket, tickets/api.py 263–288).

**(12) Run history** — `C.panel("Runs", [ C.runHistory(runs.runs) ])` (D11 #15).

**(13) Event log** — `C.panel("Events", [ C.eventLog(evs.events) ])` (D11 #14).

**(14) Chat** — `C.panel("Chat", [chatWrap])`. `chatWrap` = `<div data-chat>` containing `C.chatPanel({ entityId: id })` (T15, §7). The `[data-chat]` wrapper is T16's; T15's component owns its internal DOM/attributes.

### 3.3 Private helpers in `screens-ticket.js`

- `marker(v)` — same as board (`chip` + `data-marker`).
- `fetchText(path)` — `return fetch(path).then(function(r){ if (!r.ok) return r.text().then(function(t){ throw errorFrom(r, t); }); return r.text(); });` — a tiny text fetch so `copy-text` is not run through `fetchJson`. (Reject shape need only carry `.code`/`.message` for `errorLine`; a minimal `{code:"http_error", message:"HTTP "+status}` is fine — do **not** modify api.js.)
- `copyToClipboard(text)` — `if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);` else fallback: create a hidden `<textarea>`, set value, append, `select()`, `document.execCommand("copy")`, remove; resolve/reject a Promise accordingly.
- `submit(button, errorHost, fn)` — the in-flight pattern for the link/day/sprint/copy buttons (same shape as `inFlight` in components.js; screens carry their own copy since the components helper is private to that file).
- `selectEl(options, selectedValue)` — builds a `<select>` from `[{value,label}]`, preselecting `selectedValue`.

---

## 4. `assets/app.css` — additive sections (tokens-only)

Append new blocks; every color/size/duration/font resolves through a `var(--token)` from `tokens.css` (permitted non-token literals: `0`, unitless ratios, percentages/viewport units, keywords, font-weights — matching app.css's own header rule, lines 1–4). New class names follow the kebab convention: `board-*`, `ticket-*`, `state-control-*`, `grant-control-*`, `run-history-*`, `event-log-*`.

- **Board** — `.board { display: flex; gap: var(--space-4); overflow-x: auto; }` (horizontal scroll, constraint 8); `.board-column { min-width: calc(var(--space-7) * 5); display: flex; flex-direction: column; gap: var(--space-2); }` (min-width = an existing space-token multiple; ≈240px); `.board-column-head { … }` (uses `--type-sm`, `--text-muted`, spacing tokens); `.board-column-cards { display: flex; flex-direction: column; gap: var(--space-1); }`.
- **Ticket** — single-column stack of `.panel`s (the panels already `margin-bottom: var(--space-4)`); no two-column layout (simplicity, constraint 8). `.ticket-header-chips { display: flex; gap: var(--space-1); flex-wrap: wrap; }`.
- **State control** — `.state-control { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }` + `.state-control-select`/error styling from tokens.
- **Grant control** — `.grant-control { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }`; `.grant-control-atcap { display: flex; gap: var(--space-2); }`.
- **Run history / Event log** — row layouts: `.run-history-row`, `.event-log-row { display: flex; gap: var(--space-3); … }`; `.run-history-status`/`.event-log-kind` mono via `--font-mono`, `--type-xs`; times `--text-faint`.
- **Links / day / sprint** — `.ticket-link-row`, `.ticket-day-row`, `.link-add`, `.day-assign`, `.sprint-assign` as flex rows with `--space-*` gaps.
- Native `<select>`/`<input type="date"|"text">` inherit the existing `button, textarea { font: inherit; }` where relevant; add a shared `.select`/input rule if needed, tokens-only, mirroring `.field-editor-input` (app.css 200–216).

No new token category is introduced (PRINCIPLES.md "New token categories require evidence of need"). If a genuinely new themable value is unavoidable, that is a blocker to raise, not an inline literal.

---

## 5. `orchestration/tickets/T16-board-ticket/smoke.py`

Model directly on `orchestration/tickets/T14-ui-foundation/smoke.py`: in-process uvicorn on a daemon thread, temp DB, `free_or(PORT)`, `wait_ready`, `ok NN —` numbered prints, `try/finally` teardown (close client/page/browser, stop playwright, `server.should_exit`, join, rmtree), final `SMOKE PASS (N checks)`, any failed assertion → traceback + exit 1.

**Differences from T14's env (constraint 7):**
- `PLAN_TEST_MODE = "1"` but **NO `PLAN_FAKE_NOW`** — `seed_demo` stamps real wall-clock (`time.time()`, `datetime.now()`, seed/demo.py 29–30); a fake clock would skew the day/deadline alignment the demo builds against `today`.
- Keep `PLAN_DB_PATH`, `PLAN_PORT`, `PLAN_LOGS_DIR`, `PLAN_DISPATCHER_LOCK_PATH`, `PLAN_WS_POLL_MS="50"` as in T14 (tight flush cadence; ui_debounce stays 250).
- Gateway resolves to the **echo fake** by default in test mode (`gateway_adapter` default `"auto"` → `fake` → `EchoGatewayAdapter`, online, `available=True`, replies `"echo: <text>"`; registry.py 61–69, fakes.py 86–99). So the chat assertion follows the **online** branch.

**Setup (before the browser drives anything):**
1. Boot server, `wait_ready`. Seed: `client.post("/api/seed", json={"demo": True})` → 200 (seed/api.py 37–58; requires empty DB, which the temp DB is).
2. `client.get("/api/tickets")` → map title → id. Capture:
   - `t_success` = "Draft the onboarding email success criteria." (needs_success, ceiling needs_success, at_cap propose)
   - `t_impl`    = "Implement the demo feature slice." (in_progress, P0)
   - `t_flaky`   = "Fix the flaky login test." (in_progress, P0)
   - `t_review`  = "Review the analytics dashboard numbers." (needs_review — for the relates target)
   - `t_dropped` = "Prototype the voice input toggle." (dropped — must be absent from the board)
3. **(a) Park a pending proposal** on `t_success`: `client.post("/api/tickets/"+t_success+"/propose/success", json={"body":"Smoke success body."}, headers={"X-Plan-Actor":"agent"})` → 200. Plain-agent context (actor "agent", no claim → `is_claimed_agent` False, no `require_claim`; authctx 57–65). At ceiling `needs_success` with `at_cap=propose`, `success` is the gating field, advance-target `needs_approach` > ceiling → proposal parks (§4.4.2–3). Board `has_pending_proposal` = `gating_field_pending` → true.
4. **(b) Live claim + two runs** on `t_impl`, via direct sqlite (sanctioned — smoke owns the temp DB, dispatcher out of scope): open `sqlite3.connect(db_path)`; `UPDATE tickets SET claim_lock='claim_smoke01', claim_expires=<now+3600> WHERE id=?`; `INSERT INTO runs (id,ticket_id,status,started_at,ended_at,summary,error,pid) VALUES ('run_smoke_run',?,'running',<now-120>,NULL,NULL,NULL,NULL)` and `('run_smoke_done',?,'done',<now-600>,<now-300>,'demo done run',NULL,NULL)` (columns per db.py 128–138; status ∈ the CHECK set). commit, close. Board `has_running_claim` for `t_impl` = `has_active_claim` → true.
5. **(c) Auto-block** `t_flaky`: `UPDATE tickets SET auto_blocked=1 WHERE id=?`, commit. (For the unblock round-trip; `t_flaky` is a `blocks` **source**, so it is not itself link-blocked.)
6. Launch chromium; **grant clipboard perms** on the context: `context.grant_permissions(["clipboard-read","clipboard-write"])` (Playwright: create context, then page). `page.goto(base + "/")`, wait for `window.__plannerDebug.wsOpens >= 1`.

**Assertions (Playwright DOM against `#/board` and `#/ticket/<id>`; number every one):**

1. **Board columns + counts + dropped hidden.** goto `#/board`; wait for `[data-screen="board"]`. Assert exactly six `[data-column]`, in order `needs_success, needs_approach, needs_plan, in_progress, needs_review, done`. Card counts: needs_success 1, needs_approach 1, needs_plan 1, in_progress 2, needs_review 1, done 1. Assert `t_dropped`'s title text appears in **no** `[data-card]`.
2. **Markers + card chips.** The `t_success` card (`[data-card][data-ticket-id=t_success]`) carries `[data-marker="pending-proposal"]`; the `t_impl` card carries `[data-marker="running-claim"]`. A card shows priority/deadline/project chips (assert the `t_success` card has a priority chip, a deadline chip, a project chip present).
3. **Navigate via card click.** Click the `t_success` card; wait for `location.hash === "#/ticket/"+t_success`. Assert `[data-screen="ticket"][data-ticket-id=t_success]`, `[data-state="needs_success"]`, and the `[data-field="success"]` section hosts the proposal card (T15's proposal element — see §7 assumed attribute `[data-proposal-accept]` inside `[data-field="success"]`).
4. **Accept-with-grant from the ticket screen.** Inside `[data-field="success"]`, use T15's grant-pair picker (assumed `[data-grant-pick-ceiling]` select + `[data-grant-pick-at-cap]` radios, §7): choose a `next_ceiling` (e.g. `needs_approach`) and an `at_cap` (e.g. `propose`), click `[data-proposal-accept]`. Wait for the flush re-render; assert `[data-state="needs_approach"]` and the `success` `[data-field]` markdown block now shows "Smoke success body." (value ← proposal body; advance one step, §4.4.4/§4.4.7; `advance_target(needs_success, …)` = `needs_approach`, machine.py 40–48).
5. **Copy.** Click `[data-copy]`. Read the clipboard via `page.evaluate("navigator.clipboard.readText()")` and assert it equals `client.get("/api/tickets/"+t_success+"/copy-text").text` byte-for-byte. **Fallback** (if headless clipboard read returns empty/blocked): assert the `[data-copy]` button text became "Copied" AND that the direct `copy-text` GET returns the expected block (title line + `state:` + `priority:` + field sections + `recap:` + `links:`), proving the data path even when the browser clipboard is unreadable.
6. **Links add/remove.** In `[data-link-add]`: set `to_id` = `t_flaky`, kind = `relates`, click Add. Wait for flush; assert a `[data-link-row][data-link-kind="relates"]` appears whose text references `t_flaky`. Click that row's Remove; wait for flush; assert the `relates` row is gone.
7. **Run history + event log.** goto `#/ticket/`+`t_impl`; assert `[data-run-history]` has exactly two `[data-run-row]`, one `[data-run-status="running"]` and one `[data-run-status="done"]`. Then (event log — asserted on `t_success`, the accepted ticket) goto `#/ticket/`+`t_success`; assert `[data-event-log]` contains a `[data-event-row][data-event-kind="ticket_created"]` and rows `[data-event-kind="proposal_accepted"]` and `[data-event-kind="state_changed"]` (the accept from item 4). (Runs live on `t_impl`; the accept events live on `t_success` — the two halves are asserted on their respective screens.)
8. **Grant control.** On `t_success`: set `[data-grant-ceiling]` = `needs_plan`, `[data-grant-at-cap]` = `stop`, click `[data-grant-save]`. Wait for flush; assert the re-rendered `[data-grant-ceiling]` value is `needs_plan` and the `stop` radio is checked (grant persisted, `change_grant`).
9. **State control + unblock + drop.** On `t_success`: set `[data-state-select]` = `needs_plan`, click `[data-state-jump]`; wait for flush; assert `[data-state="needs_plan"]`. Then goto `#/ticket/`+`t_flaky`; assert an auto-blocked marker (`[data-marker="auto-blocked"]`) and `[data-unblock]` present; click `[data-unblock]`; wait for flush; assert the auto-blocked marker is gone. Then goto `#/ticket/`+`t_success` (now `needs_plan`), click `[data-drop]`; wait for flush; goto `#/board`; assert no `[data-card][data-ticket-id=t_success]` in any column (it dropped out of the `needs_plan` column it sat in after the jump).
10. **Chat.** goto `#/ticket/`+`t_impl` (a live ticket); assert `[data-chat]` present. Minimal exercise against the online echo fake: locate the first `textarea`/`input` inside `[data-chat]`, fill "smoke hello", click the panel's send control, wait for the text "echo: smoke hello" to appear inside `[data-chat]`. **Fallback** (T15 internal DOM differs / control not found): assert `[data-chat]` present AND `client.get("/api/chat/"+t_impl+"/status").json()["available"] is True` (proves the online path; chat/api.py 38–43, echo fake `available=True`).

Print `SMOKE PASS (N checks)` and return 0.

---

## 6. data-* attribute inventory (VERBATIM — e2e T18/T19 select on these)

| Screen / region | Attribute(s) |
|---|---|
| Board root | `[data-screen="board"]` |
| Board column | `[data-column="<state>"]` |
| Board card | `[data-card]` `[data-ticket-id="<id>"]` |
| Board card markers | `[data-marker="pending-proposal"]` · `[data-marker="running-claim"]` |
| Ticket root | `[data-screen="ticket"]` `[data-ticket-id="<id>"]` `[data-state="<state>"]` |
| Ticket header markers | `[data-marker="running-claim"]` · `[data-marker="auto-blocked"]` · `[data-marker="blocked"]` (reuse `[data-marker=…]`) |
| Field sections | `[data-field="success|approach|plan|result"]` |
| Recap | `[data-recap]` |
| State control | `[data-state-control]` with `[data-state-select]`, `[data-state-jump]`, `[data-drop]`, `[data-unblock]` |
| Grant control | `[data-grant-control]` with `[data-grant-ceiling]`, `[data-grant-at-cap]`, `[data-grant-save]` |
| Copy | `[data-copy]` |
| Links | `[data-links]`, rows `[data-link-row]` `[data-link-kind="<kind>"]`, add form `[data-link-add]` |
| Day assign | `[data-day-assign]`, rows `[data-day-row]` |
| Sprint assign | `[data-sprint-assign]` |
| Run history | `[data-run-history]`, rows `[data-run-row]` `[data-run-status="<status>"]` |
| Event log | `[data-event-log]`, rows `[data-event-row]` `[data-event-kind="<kind>"]` |
| Chat | `[data-chat]` (wrapper is ours; T15's component may carry its own internal attributes) |

---

## 7. T15 interface assumptions (EXTERNAL CONTRACTS — reconcile against T15's landed code before/at integration)

T15 owns D11 items 7 (proposal card) + 8 (grant-pair picker) + 10 (chat panel) and adds them to `Planner.components`. T16 calls them; it does not define them. The assumed signatures below let T16 be written now; when T15 lands, the diff to reality must be mechanical (rename a key / an attribute), not structural. If a mismatch is structural, that is an integration blocker to log, not to paper over.

**`Planner.components.proposalCard(opts) → DOM node`** — assumed:
- `opts.proposal` = the `fields[field].proposal` object `{body, proposed_by, created_at}`.
- `opts.field` = the field name string (label / disambiguation).
- `opts.requireGrant` = boolean. T16 passes `true` on every ticket-screen proposal card (mirrors Review; the grant-pair picker shows and Accept is gated on both halves per D11 #8). The accept endpoint always accepts the pair; on a non-gating field the server ignores it (resolution.py), so passing `true` uniformly is safe.
- `opts.onAccept` = `function({edited_body, next_ceiling, at_cap}) → Promise`. `next_ceiling` already in wire form (a `TicketState` value or `"none"`); `at_cap` = `"stop"|"propose"`; `edited_body` = the quick-edit text or `null`/absent. T16 forwards these verbatim to `POST /api/tickets/{id}/accept/{field}`. Resolve on success (no manual re-render), reject with the `errorLine`-shaped error on failure.
- Assumed internal attributes the smoke drives (item 3/4), to confirm against T15: grant-pair ceiling select `[data-grant-pick-ceiling]`, at-cap radios `[data-grant-pick-at-cap]`, Accept button `[data-proposal-accept]`, quick-edit textarea (optional). If T15 named them differently, update the smoke selectors only.

**`Planner.components.chatPanel(opts) → DOM node`** — assumed:
- `opts.entityId` = the ticket id (chat entity id = ticket id; §11). The panel self-manages: on mount it may `GET /api/chat/{entityId}/status`, and on send `POST /api/chat/{entityId}/send {text}` → `{reply_text, session_key}` (chat/api.py), appending the user line + reply locally (chat is interactive, not WS-flush-driven). Offline → renders a "gateway offline" notice (§11).
- T16 wraps the returned node in `<div data-chat>`; the smoke's chat exercise (item 10) reaches into the panel generically with a resilient fallback because the panel's internal DOM is T15's.

---

## 8. Out of scope / explicitly NOT done here

- **`_SHELL` script tags in `src/planner/core/server.py`** — orchestrator glue. T16 does not edit server.py. Required order: the two new tags **after** `/assets/app.js`.
- **`assets/app.js`, `assets/api.js`, `assets/config.js`, `assets/markdown.js`, `assets/tokens.css`** — not touched. Screens use literal API path strings (`/api/board`, `/api/tickets/…`, etc.); api.js gains no new helper (the plain-text `copy-text` is fetched with a local `fetchText`, not `fetchJson`).
- **T15's components** (`proposalCard`, `grantPairPicker`, `chatPanel`, `planTree`, `reviewCard`) — reused/assumed, never redefined in components.js.
- **Overdue accents on the board** — no client-side overdue computation; the deadline chip is plain (the server sends no overdue flag on board cards, and client date math would drift from the server planning date).
- **Drag-and-drop on the board** — v1 non-goal (§10.3); the card is a plain click-through anchor.
- **Optimistic UI / client state store** — none; the WS flush is the sole re-render trigger after a successful mutation.
- **pytest tests** — none belong to T16. The named e2e items 22/23/30/31 are T18/T19's. Gate: `node --check` green on every `assets/*.js`, tokens-only styling, the smoke passing, and the existing suite staying green (T16 touches no Python besides the new smoke, which lives outside `tests/`).

---

## 9. Behaviour → SPEC / D11 map

| Behaviour | Source |
|---|---|
| Board: one column per state, `dropped` hidden, cards title/priority/deadline/project + pending-proposal + running-claim markers, click opens Ticket, no DnD | §10.3; `board_view` tickets/views.py 224–259 |
| Ticket: four field sections, value as markdown | §10.4; §4.2 |
| Pending proposal → proposal card, Accept/Edit carry the onward grant | §10.4; §4.4.4/§4.4.7; D11 #7/#8 |
| Notes editable inline | §4.2; §10.4 |
| Recap section | §10.4 |
| State control (jump / drop / unblock) | §10.4 "state control"; §4.1 (drop human-only, any→dropped); §7.5 (unblock clears auto_block); D11 #11 |
| Grant control (plain ceiling + at-cap pickers, no dial) | §10.4; §4.3; D11 #12 |
| Copy ticket → plain-text block to clipboard | §10.4 |
| Links add/remove, four kinds | §3.6; §10.4 |
| Day / sprint assignment (sprint derived when parented) | §10.4; §3.3 (`sprint_id` writable only when `sprint_item_id` NULL) |
| Run history | §10.4; §7.3; D11 #15 |
| Event log | §10.4; §9 (events); D11 #14 |
| Chat panel (entity id = ticket id) | §10.4; §11; D11 #10 |
| Refresh restores identical state; render from server JSON; WS invalidation → refetch | §9; §10 |
| Tokens-only styling, five-size type scale, kebab classes | §10 design system; PRINCIPLES.md |

---

## 10. BINDING AMENDMENTS (post codex review + T15 landing — these override §§1–7 where they conflict)

T15 (and T17) have landed in the shared files since this plan was written. Codex review
(plan-review.md) accepted 7 findings. The implementer builds to the plan AS AMENDED here.

**A1 — Shared-file edit protocol (team-lead directive, verbatim).** components.js, app.js, and
app.css are SHARED with T15/T17. Modify them only with targeted Edit operations (exact-string
replace/append at your own anchor), NEVER a full-file Write — a Write would clobber a sibling's
concurrent additions. Re-read the shared file right before each edit. screens-board.js and
screens-ticket.js are exclusively T16's. (app.js needs no edit at all.)

**A2 — components.js reuse (replaces §1.1's private helpers).** T15's additions are in and
exported: use `Planner.components.STATE_ORDER`, `.gatingField(state)`, `.advanceTarget(state,
ceiling)`, `.quietLine(text)`, and (privately, same IIFE) `bindMutating(button, container, run)`
and `make(tag, className, text)`. Do NOT add a second STATE_ORDER const or an `inFlight` helper —
`bindMutating` IS the in-flight discipline (note: it stays disabled after success by design; the
flush re-render replaces the DOM). Still added by T16: `stateLabel` (if not present), `formatUnix`,
`eventSummary` — exactly as §1.1 specifies. Insert the T16 block after T15's block (anchor: the
`reviewCard` function's end / just before the `Planner.components = {` export), and extend the
export object by Edit (e.g. anchor `reviewCard: reviewCard` → add the four new entries after it).

**A3 — grantControl composes the T15 picker (replaces §1.3 DOM).** Signature:
`grantControl(opts)`, `opts = { state, ceiling, atCap, onSave(ceiling, atCap) → Promise }`.
DOM:
```
div.grant-control              [data-grant-control]
  div.grant-control-current    [data-grant-current]   → text "ceiling: <opts.ceiling> · at-cap: <opts.atCap>"
  <grantPairPicker(opts.state, sync)>                 // T15 component: [data-grant-ceiling] select ("No further"/"none" + states ≥ opts.state), [data-grant-atcap] radios
  button.button [data-grant-save] "Save"              // disabled until picker.getGrant() !== null
  div.grant-control-error
```
Save click (bindMutating discipline, but with the disabled-until-picked gate): map
`g = picker.getGrant()`; `ceiling = (g.next_ceiling === "none") ? opts.state : g.next_ceiling`;
call `opts.onSave(ceiling, g.at_cap)`. "No further" = ceiling becomes exactly the current state —
a ceiling below the current state is not offered (behaviourally identical to at-cap) and this is
D11 #8's sanctioned option set. No T16-built select/radio internals (codex finding 2). The
`[data-grant-at-cap]` name in §6's inventory is REPLACED by T15's `[data-grant-atcap]` (finding 5);
smoke drives radios via `[data-grant-control] [data-grant-atcap] input[value=...]` and asserts
persistence via the re-rendered `[data-grant-current]` text.

**A4 — proposalCard call sites (replaces §7 assumptions; landed signature).** For each field
with a non-null proposal:
```
C.proposalCard({
  proposal: detail.fields[name].proposal,
  requireGrant: name === C.gatingField(detail.state),        // finding 3: gating field only
  newState: C.advanceTarget(detail.state, detail.ceiling),   // picker floor (only read when requireGrant)
  onAccept: function (payload) {                              // payload assembled BY the card:
    return api.fetchJson("/api/tickets/" + id + "/accept/" + name,   // {edited_body?} iff textarea differs,
                         { method: "POST", body: payload });         // + {next_ceiling, at_cap} iff requireGrant
  }
})
```
Card internals (T15's, for the smoke only): Accept `[data-accept]`, edit textarea `[data-edit]`,
picker `[data-grant-ceiling]` / `[data-grant-atcap]`. Scope every smoke selector under
`[data-field="..."]` — the grant control hosts a second `[data-grant-ceiling]` on the same screen.

**A5 — chatPanel call site (replaces §7 assumptions; landed signature).** Add a FIFTH parallel
fetch to §3.1: `api.fetchJson("/api/chat/" + id + "/status")` → `{available}`. Section (14)
becomes: `C.panel("Chat", [chatWrap])` where `chatWrap` = `<div data-chat>` containing
`C.chatPanel(id, { available: available })` (positional entityId — day-screen precedent,
screens-day.js line 135). Replies paint locally; the smoke's chat assertion waits for a
`[data-chat-msg="planner"]` node containing "echo: smoke hello" — NO flush wait, and the
offline branch is dead in test mode (echo fake, available true).

**A6 — link rows (finding 6).** Every row renders `kind: from_id → to_id` (both endpoints,
always in link direction). Each endpoint id that starts with `t_` AND is not the current ticket
renders as an anchor `#/ticket/<id>`; other ids (and the current ticket's own id) are plain text.

**A7 — smoke shell precondition (findings 1+7).** The ORCHESTRATOR adds the two script tags to
`_SHELL` (after `/assets/app.js`; T15's tags are already there) before the smoke runs; the
implementer never edits server.py. The smoke's first check asserts `GET /` contains
`/assets/screens-board.js` and `/assets/screens-ticket.js` (ordered after `/assets/app.js`) and
both serve 200 — loud failure if the glue is missing. All screen-root waits use
`section[data-screen="..."]` (finding 4 — nav anchors also carry data-screen).

**A8 — inventory deltas (consolidated).** In §6's table: `[data-grant-at-cap]` →
`[data-grant-atcap]` (T15's, radios inside); grant control gains `[data-grant-current]`;
proposal-card internals documented as T15's `[data-accept]`/`[data-edit]`/`[data-grant-ceiling]`/
`[data-grant-atcap]`; chat internals documented as T15's `[data-chat-panel]`/`[data-chat-input]`/
`[data-chat-send]`/`[data-chat-msg]`/`[data-chat-offline]` under T16's `[data-chat]` wrapper.
Everything else in §6 stands verbatim.
