# T14 plan — UI foundation: tokens, shell, data layer, primitives

**Ticket:** orchestration/tickets/T14-ui-foundation/ticket.md (stage 5).
**Contracts read:** decisions.md D11 (binding component inventory — items 1, 2, 3, 4, 5, 6, 17 only); PRINCIPLES.md design-system section; SPEC §9 (WS = invalidation signal only, debounce 250ms), §10 (six screens, simplicity ruling, token-file rules), §14 (audio-capture seam), §15 (non-goals); T01 plan §13 (route table + route ownership); the live JSON producers on disk: `src/planner/tickets/api.py` + `views.py`, `src/planner/sprints/api.py` + `views.py`, `src/planner/days/api.py`, `src/planner/dispatch/api.py`, `src/planner/chat/api.py`, `src/planner/core/errors.py` (`to_payload()` → `{"error":{code,message,detail}}`), `src/planner/core/server.py` (meta keys `ui_debounce_ms`/`ws_poll_ms`/`test_mode`; `_SHELL`; `/assets` mount at cwd-relative `assets/`), `src/planner/core/ws.py` (`{events:[{id,entity_id,kind,payload,created_at}...], cursor}` non-empty batches, server poll `ws_poll_ms`), `src/planner/core/config.py` (ui_debounce_ms 250, ws_poll_ms 300, both env-overridable — `PLAN_WS_POLL_MS` exists at config.py:147), `assets/tokens.css` starter.

**Approach.** Five classic scripts compose under one namespace object `window.Planner` (each file is an IIFE that does `window.Planner = window.Planner || {}` and attaches exactly one sub-namespace), so seven asset files load in `<script src>` order with no bundler and every file passes `node --check`. The data layer is deliberately stateless: `api.js` holds only a WS cursor, a debounce timer, a backoff counter, and one registered flush callback; every flush makes `app.js` re-run the active screen's `render(root, params)`, which refetches full JSON — WS payloads are never read beyond `msg.cursor` (SPEC §9 audit target). Rendering primitives are the seven D11 components, all styled in `app.css` exclusively through `var(--token)`; the whole personality (restrained neutral-dark surfaces, one amber accent meaning "needs the human", five type sizes, ease-out motion) lives in `tokens.css`. The server change is confined to the `_SHELL` string. The smoke gate boots in-process uvicorn in test mode and drives real headless chromium to prove: shell references in order, meta keys, default route + placeholders, and the event-append → one-debounced-flush invariant (single event ⇒ exactly 1 flush; two rapid events ⇒ exactly 1 flush).

---

## 0. Hard constraints (restated; the implementer and reviewers hold these)

1. Every JS file passes `node --check`: classic scripts, **no** `import`/`export`/module syntax, no top-level `await`. Globals only via `window.Planner` (plus the one sanctioned debug hook `window.__plannerDebug`, see §5).
2. Zero client-side state stores. No caches of entity JSON, no reconciliation of WS payloads. The only client state: the WS cursor for `?since=`, the debounce timer, the backoff delay, the registered callback, and the active route (which lives in the URL hash, nowhere else). Refresh restores everything from the URL.
3. Design bar: restrained dark surfaces, ONE deliberate accent, five type sizes max, all motion from motion tokens, entrances ease out, nothing bounces; the entire personality tunable from `tokens.css` alone; empty states are ONE quiet line, never instructional; no explanatory text for the obvious.
4. `app.css` grep audit: **no literal colors, no `px`/`rem`/`em`/`ms`/`s` literals** anywhere — every color/size/duration is `var(--token)` (or `calc()` over tokens). Permitted non-token values, stated for the auditor: `0`, unitless ratios (`line-height: 1.5`, `opacity: .5`, `flex: 1`), percentages/viewport units (`100%`, `100vh`), keywords (`auto`, `none`, `transparent`, `inherit`), and font-weights (`500`, `600`) — none of these is a themable color/duration/size. Audit commands in §12.
5. `.venv/bin/ruff check .` and `.venv/bin/mypy src/` stay green. The only Python touched: the `_SHELL` constant in `src/planner/core/server.py` (nothing else in that file — another ticket edits the repo concurrently; the assert, lifespan, routers, meta, ws, test router, and mount stay byte-identical) and the new `smoke.py` (ruff-clean: line length 100, rules E/F/W/I/UP/B; not under `src/`, so mypy does not see it).
6. `.venv/bin/pytest tests/unit -q` stays green. Verified before planning: no test reads `GET /` or `_SHELL` (grep over `tests/` for `_SHELL` and `get("/")` is empty; `test_chat_seed.py` uses TestClient but only hits `/api/*`). The `_SHELL` rewrite cannot break the unit suite.
7. Nothing beyond D11 items 1–6 and 17. §15 non-goals bind: no command palette, no keyboard-shortcut layer (⇒ zero `keydown` handlers in this ticket), no drag-and-drop, no instructional empty states, no diff-style proposal views, no staleness widgets.
8. Audio seam (§14): the chat panel is a LATER ticket (D11 item 10). This foundation must merely not preclude a non-text input source — and it doesn't: nothing here references chat, the bus is transport-agnostic, and no component assumes where content strings come from. No chat code lands in T14.

---

## 1. `assets/tokens.css` — final values

Keep the file's header comment (updated to say "tuned in stage 5" → "stage-5 values"). Categories: surfaces, text, accent, radius, motion, spacing, borders — plus **type** (decision argued below). No other categories.

### The type-scale decision (this is the "evidence of need" case — argued)

Add a `type` token category: five sizes plus two font stacks. Evidence of need, three independent lines:
1. **The ticket itself names it**: "refine starter values into the real personality: … five-size type scale …" lists the type scale as tokens.css content (ticket.md line 11). The "no new token categories" sentence in the same line cannot exclude a thing that same line requires; it forbids categories beyond the enumerated personality.
2. **The zero-literal constraint forces it**: `app.css` may contain no literal sizes. Font sizes are sizes; without `--type-*` tokens a five-size scale is inexpressible.
3. **SPEC §10 / PRINCIPLES**: the token file "owns everything themable"; a *strict* five-size cap needs one enforceable home — five tokens make the cap grep-auditable (any sixth size would be a sixth token or a banned literal).

The two font-stack tokens ride in the same category on the same logic: type personality is themable, and font-family strings in `app.css` would be untunable literals. No line-height or weight tokens — line-heights are unitless ratios and weights are not colors/durations/sizes; adding tokens for them is speculative (PRINCIPLES: no speculative categories).

### Final token table

| Token | Value | Why |
|---|---|---|
| `--surface-base` | `#111214` | Page ground. Starter surfaces had a blue cast; with one warm accent the surfaces go achromatic-neutral so the accent is literally the only hue on screen — "one deliberate accent" enforced by the palette itself. |
| `--surface-raised` | `#191b1e` | Panels — one quiet step up; the Panel is the only box primitive, this is its fill. |
| `--surface-overlay` | `#22252a` | Hover fills, chip fills, hairline separators — the third and last neutral step. |
| `--surface-sunken` | `#0b0c0e` | Code blocks and textarea wells sit *below* the page — editing surfaces read as inset. |
| `--text-strong` | `#f2f3f5` | Titles and the row a pointer is on. |
| `--text-default` | `#c6cad0` | Body. Comfortable dark-surface contrast without glare. |
| `--text-muted` | `#8d939b` | Secondary: nav resting state, quiet chips, empty-state lines. |
| `--text-faint` | `#5d636b` | Lowest rung: frozen markers, hairline borders on quiet chips. |
| `--accent-bright` | `#e2a33e` | THE accent — warm amber, not default-dashboard blue. Semantic role fixed here: **the accent means "needs the human"** (pending proposal, review badge, overdue, auto-blocked, P0, errors, focus, primary action). Everything else stays achromatic. |
| `--accent-surface` | `#33270f` | Dark amber wash — fills for attention chips, the badge, primary button. |
| `--accent-text` | `#f4e3bd` | Text sitting on `--accent-surface` fills. |
| `--radius-sm` | `4px` | Inputs, buttons, nav pills, inline code. (unchanged) |
| `--radius-md` | `8px` | Panels, pre blocks. (unchanged) |
| `--radius-lg` | `12px` | Was 14px; 4→8→12 is an even scale — 14 was an off-rhythm jump. Reserved for large surfaces (screens may use it later). |
| `--radius-pill` | `999px` | Chips and the badge. (unchanged) |
| `--motion-fast` | `90ms` | Hovers, focus rings. (unchanged) |
| `--motion-base` | `150ms` | Was 160ms; rounded into a 90/150/240 rhythm. Buttons, chips, background shifts. |
| `--motion-slow` | `240ms` | Was 280ms — "smooth and decisive, never slow"; 240ms entrance is decisive. Screen entrances only. |
| `--motion-ease` | `cubic-bezier(0.2, 0.7, 0.3, 1)` | Ease-out (fast start, soft landing), overshoot-free — entrances ease out, nothing bounces. (unchanged) |
| `--space-1`…`--space-7` | `4/8/12/16/24/32/48px` | Starter rhythm is right (4px base, gentle acceleration); unchanged. |
| `--border-hairline` | `1px` | Panel and chip borders. (unchanged) |
| `--border-strong` | `2px` | Focus outlines, error-line rule. (unchanged) |
| `--font-ui` | `system-ui, -apple-system, "Segoe UI", sans-serif` | NEW (type category). Native stack: zero-load, calm, non-branded. |
| `--font-mono` | `ui-monospace, "SF Mono", Menlo, monospace` | NEW. Code spans/blocks, error codes, the markdown-editing textarea. |
| `--type-xs` | `11px` | NEW. Chips, badge, error codes — dense metadata. |
| `--type-sm` | `13px` | NEW. Nav, secondary text, code, quiet lines. |
| `--type-md` | `15px` | NEW. Body. |
| `--type-lg` | `19px` | NEW. Panel titles, markdown h1. |
| `--type-xl` | `24px` | NEW. Screen titles — reserved for T15–T17; defined now so the five-size cap is complete and closed. |

**Changed from starter, summarized:** blue accent → amber (the one deliberate accent, with a fixed meaning), blue-cast surfaces → pure neutrals (so the accent is the only hue), `--radius-lg` 14→12 (even scale), `--motion-base/slow` 160/280→150/240 (decisive), type category added (argued above). Everything else is the starter value kept on purpose — restraint is the personality.

---

## 2. `assets/config.js` — frontend constants

Whole file (shape is normative; the implementer writes exactly this module):

```js
/* Frontend constants. Anything tunable on the client lives here, not inline.
 * Classic script: attaches Planner.config. */
(function () {
  "use strict";
  var Planner = (window.Planner = window.Planner || {});
  Planner.config = {
    // Used only until GET /api/meta answers (SPEC §9: debounce 250ms from /api/meta).
    DEBOUNCE_MS_DEFAULT: 250,
    // WS reconnect backoff: 500 → 1000 → 2000 → 4000 → 8000 → 10000 (capped).
    WS_RETRY_MIN_MS: 500,
    WS_RETRY_MAX_MS: 10000,
    WS_RETRY_FACTOR: 2,
    // API paths the foundation itself calls.
    API: {
      META: "/api/meta",
      QUEUES: "/api/queues",
      EVENTS_WS: "/api/events"
    },
    // Hash routes (screen registry keys are the values' first segment).
    ROUTES: {
      day: "#/day",
      review: "#/review",
      board: "#/board",
      sprint: "#/sprint",
      backlog: "#/backlog",
      ticketPrefix: "#/ticket/"
    }
  };
})();
```

No screen-endpoint catalogue beyond what T14 calls — T15–T17 own their endpoints; putting them here now would be speculative.

## 3. `assets/api.js` — fetch wrapper, WS client, invalidation bus

Attaches `Planner.api` and `Planner.bus`; defines `window.__plannerDebug`. IIFE, `"use strict"`.

### `Planner.api.fetchJson(path, options) -> Promise<object>`

- `options` optional: `{ method?: string, body?: object }`. When `body` present: `JSON.stringify` it, header `Content-Type: application/json`. Default method GET.
- 2xx → `response.json()`; a JSON parse failure rejects with `code:"bad_json"`.
- Non-2xx → read body, try `JSON.parse`; if it carries the PlannerError envelope (`parsed.error && parsed.error.code`) reject with an `Error` whose `message = parsed.error.message` and with properties `code = parsed.error.code`, `detail = parsed.error.detail`, `status = response.status`, `isPlannerError = true` (errors.py `to_payload()` shape: `{"error":{code,message,detail}}`). Otherwise reject with `code:"http_error"`, `message:"HTTP " + status`, `status`.
- Network-level failure (fetch rejects) → normalized to `code:"network"`, `message:"network error"`. Every rejection therefore carries `code` + `message`, which is exactly what `errorLine` renders (D11 item 17).

### `Planner.bus` — the invalidation bus (SPEC §9 audit target)

State (the complete list — nothing else): `cursor` (int, starts 0), `socket`, `debounceMs` (set by `start`), `flushTimer`, `retryMs` (starts `WS_RETRY_MIN_MS`), `onFlush` (single callback), `started` (bool guard).

- `Planner.bus.onInvalidate(fn)` — registers THE single flush callback (last write wins; `app.js` is the only caller).
- `Planner.bus.start(debounceMs)` — stores `debounceMs`, sets `started`, calls internal `connect()`. Idempotent (second call ignored).
- `connect()`: `new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + Planner.config.API.EVENTS_WS + "?since=" + cursor)`.
  - `onopen`: `retryMs = WS_RETRY_MIN_MS`; `__plannerDebug.wsOpens += 1`; one quiet `console.debug("[planner] ws open since=" + cursor)`.
  - `onmessage`: `var msg = JSON.parse(e.data)` inside try/catch (parse failure → one `console.debug`, return — the bus never throws). Then **the only field ever read is `msg.cursor`**: `if (typeof msg.cursor === "number") { cursor = msg.cursor; __plannerDebug.cursor = cursor; }` and `schedule()`. `msg.events` is never inspected, stored, or reconciled — the batch is purely an invalidation signal.
  - `schedule()`: `clearTimeout(flushTimer); flushTimer = setTimeout(flush, debounceMs)` — classic trailing debounce. (Server-side poll is `ws_poll_ms` (default 300ms) per ws.py, so distinct batches arrive ≥ poll apart; back-to-back full-batch continuations coalesce into one flush, which is the point. Starvation is impossible: quiet gap ≥ poll interval > nothing; the timer always fires.)
  - `flush()`: `flushTimer = null; __plannerDebug.flushes += 1; console.debug("[planner] flush " + __plannerDebug.flushes); if (onFlush) onFlush();` — ONE quiet line per flush, the console hook the ticket sanctions.
  - `onclose` (reconnect handled here only — `onerror` is always followed by `close`, handling both would double-schedule): `socket = null; setTimeout(connect, retryMs); retryMs = Math.min(retryMs * WS_RETRY_FACTOR, WS_RETRY_MAX_MS)`. Reconnect reuses the current `cursor` in `?since=` — ws.py replays every event row with id > cursor, so nothing is missed and nothing needs client buffering.

### Debug hook (sanctioned by ticket acceptance "observable via console hook")

```js
window.__plannerDebug = { flushes: 0, wsOpens: 0, cursor: 0 };
```
Counters only; one `console.debug` line per flush and per socket open; nothing else logs.

### Event → debounce → refetch sequence (normative)

```
writer appends events row(s)                                   (any mutating endpoint)
  └─ ≤ ws_poll_ms later: ws.py poll finds rows > cursor
       └─ push {events:[...], cursor: N}
client onmessage
  ├─ cursor := N                     ← the ONLY thing read from the batch
  └─ schedule(): clearTimeout; setTimeout(flush, debounceMs)
       [more batches inside the window merely reset the timer → they coalesce]
timer fires → flush()
  ├─ __plannerDebug.flushes += 1 ; console.debug("[planner] flush n")
  └─ onFlush()                       ← exactly one notification, app.js's handler:
        ├─ refreshBadge(): GET /api/queues → shell.setReviewBadge(approvals.length)
        └─ route(): re-render active screen — render() refetches full JSON, no cache
socket drops
  └─ onclose: setTimeout(connect, retryMs); retryMs := min(retryMs×2, 10000)
connect() → GET ws /api/events?since=<cursor>   (resume; server replays > cursor)
onopen → retryMs := 500 ; wsOpens += 1
```
`debounceMs` = `meta.ui_debounce_ms` (250 from config.py); until `/api/meta` answers, `DEBOUNCE_MS_DEFAULT` (250) from config.js. Backoff parameters all from `Planner.config`.

## 4. `assets/markdown.js` — minimal safe renderer

Attaches `Planner.markdown`. One public function:

`Planner.markdown.render(text) -> HTMLElement` — returns `div.markdown` whose innerHTML is a string built ONLY from escaped input plus renderer-authored tags.

### The pipeline, in exact order (XSS-critical — do not reorder)

1. **Sanitize sentinels**: `text = String(text).replace(/\u0000/g, "")` — NUL is the placeholder char below; stripping it first makes placeholder collisions impossible.
2. **Escape everything once, before any parsing**: `escapeHtml(s)` replaces, in this order: `&`→`&amp;`, `<`→`&lt;`, `>`→`&gt;`, `"`→`&quot;`, `'`→`&#39;`. From here on the parser only ever sees escaped text; **raw input never reaches innerHTML anywhere**. (All markdown delimiters — `` ` `` `#` `*` `_` `-` `[` `]` `(` `)` and digits — survive escaping untouched, so parsing on escaped text is lossless.)
3. **Line walk** (`escaped.split("\n")`, single pass, block accumulator):
   - line matches `/^```/` → toggle **fence**: open collects following lines verbatim until the closing ```` ``` ````; emit `<pre><code>` + collected lines joined with `\n` + `</code></pre>`. NO inline processing inside fences (content is already escaped — that is its entire treatment). An unclosed fence at EOF emits what was collected.
   - blank line → close the open paragraph/list block.
   - `/^(#{1,6})\s+(.*)$/` → heading: `<h1>`–`<h3>` for 1–3 `#`; four or more `#` render as `<h3>` (the visual scale caps at three heading levels inside markdown — five type sizes total, see §9). Heading text goes through `renderInline`.
   - `/^[-*+]\s+(.*)$/` → unordered list item; consecutive items collect into one `<ul>` of `<li>` (each item through `renderInline`). No nesting (minimal renderer).
   - `/^\d{1,9}[.)]\s+(.*)$/` → ordered list item → `<ol>`, same treatment.
   - anything else → paragraph line: each line goes through `renderInline` individually, consecutive lines join with `<br>` inside one `<p>` (planner briefs and proposals rely on single newlines; GFM-comment behavior chosen deliberately — noted in §13).
4. **`renderInline(escapedLine)`**, exact order:
   1. **Code spans first, protected**: replace `/`+`` `([^`]+)` ``+`/g` — each match's inner text is pushed to an array and substituted with `\u0000<index>\u0000`. Code content gets no further processing.
   2. **Links**: `/\[([^\]]+)\]\(([^()\s]+)\)/g` → `<a href="URL">text</a>` only if the URL passes the scheme whitelist: after lowercasing, a URL matching `/^[a-z][a-z0-9+.-]*:/` must start with `http:`, `https:`, or `mailto:`; scheme-less (relative, `#/ticket/...`, `?`, `/`) is allowed. A rejected URL leaves the source text as-is (already escaped — inert). The href value is the escaped text (quotes arrive as `&quot;` — cannot break out of the attribute).
   3. **Bold**: `/\*\*([^*]+)\*\*/g` → `<strong>$1</strong>` (before italic so `**` is not eaten by `*`).
   4. **Italic**: `/\*([^*]+)\*/g` and `/_([^_]+)_/g` → `<em>$1</em>`.
   5. **Restore code spans**: replace each `\u0000<index>\u0000` with `<code>` + stored content + `</code>`.
5. Assemble blocks into one string, create `document.createElement("div")`, `className = "markdown"`, set `innerHTML` to the assembled string, return the element.

Why this cannot get XSS wrong: (a) the ONE innerHTML assignment receives a string whose every character of user origin passed `escapeHtml` in step 2 — `<`/`>`/quotes cannot exist un-entified; (b) every tag in the string is renderer-authored from a closed set (`p br h1 h2 h3 ul ol li pre code strong em a`); (c) the only attribute ever emitted is `href`, scheme-whitelisted with `javascript:`/`data:`/`vbscript:` (and every other scheme) rejected; (d) placeholders cannot be forged (step 1). No external libs, no other innerHTML in the entire codebase (components use `createElement`/`textContent` only).

## 5. `assets/components.js` — the D11 primitives (items 1–6, 17)

Attaches `Planner.components = { appShell, panel, markdownBlock, fieldEditor, chip, entityRow, errorLine }`. All functions return DOM nodes built with `createElement`/`textContent` (never innerHTML). No framework, no near-duplicates: the chip is ONE component with variants; the panel is the ONLY box primitive.

### 5.1 `appShell() -> { el, content, setReviewBadge(n), setActiveNav(name) }` — D11 item 1

The one component returning a handle object instead of a bare node — the router needs its content slot and badge/nav setters; flagged in §13.

```
div.shell
├─ header.shell-nav
│  ├─ span.shell-brand            "planner"
│  └─ nav.shell-links
│     ├─ a.nav-link[data-screen=day][href="#/day"]         "Day"
│     ├─ a.nav-link[data-screen=review][href="#/review"]   "Review"
│     │   └─ span.nav-badge       (hidden; pending count)
│     ├─ a.nav-link[data-screen=board][href="#/board"]     "Board"
│     ├─ a.nav-link[data-screen=sprint][href="#/sprint"]   "Sprint"
│     └─ a.nav-link[data-screen=backlog][href="#/backlog"] "Backlog"
└─ main.shell-content             ← `content`; screens render here
```

**Nav-set decision:** five links. `#/ticket/<id>` requires an id, so Ticket is not a nav destination — it is reached through entity rows and board cards (`entityRow` hrefs), exactly like SPEC §10.3 "card click opens Ticket". The router still owns the `#/ticket/<id>` route (§6).

- `setReviewBadge(n)`: `n > 0` → badge `textContent = String(n)`, shown; else `display:none` via a `.hidden` class. Count = `approvals.length` from `GET /api/queues` (tickets/views.py `queues_view` → `{approvals, pickup, overdue}`) — approvals is the Review screen's pending set (§10.2).
- `setActiveNav(name)`: toggles class `active` on the `a[data-screen=name]`; unknown/`ticket` name → no link active.
- Links are plain anchors — navigation is the hash changing; no click handlers, no keyboard layer.

### 5.2 `panel(title, children) -> HTMLElement` — D11 item 2

`children`: `Node | Node[] | null`.

```
section.panel
├─ h2.panel-title      textContent = title
└─ div.panel-body      ← children appended
```

### 5.3 `markdownBlock(text) -> HTMLElement` — D11 item 3

Empty/whitespace `text` → `div.quiet-line` with textContent `(none)` (one quiet line, matching the server's copy-text convention; never instructional). Else `Planner.markdown.render(text)` with `classList.add("markdown-block")`.

### 5.4 `fieldEditor(value, onSave) -> HTMLElement` — D11 item 4

```
div.field-editor
├─ textarea.field-editor-input    rows=8, value = value || ""
└─ div.field-editor-actions
   ├─ (div.error-line — inserted here on failure, replaced per attempt)
   └─ button.button.button--primary   "Save"
```
Save click: remove any prior `.error-line` in actions; `button.disabled = true`; `Promise.resolve(onSave(textarea.value))` → on rejection `actions.prepend(errorLine(err))` (item 17 inline near the failed action); `finally` re-enable the button. No optimistic UI — after a successful save the WS event invalidates and the screen refetches.

### 5.5 `chip(variant, value, opts) -> HTMLElement` — D11 item 5 (ONE badge, variants)

Returns `span.chip.chip--<class>` with `data-value` where noted. `opts` only meaningful for `deadline`.

| variant | value | classes | text | background | text color | border color |
|---|---|---|---|---|---|---|
| `priority` | `"P0"` | `chip--priority chip--p0` | `P0` | `--accent-surface` | `--accent-text` | `--accent-bright` |
| `priority` | `"P1"` | `chip--priority chip--p1` | `P1` | `--surface-overlay` | `--text-strong` | `--text-muted` |
| `priority` | `"P2"` | `chip--priority chip--p2` | `P2` | `--surface-overlay` | `--text-default` | `--text-faint` |
| `priority` | `"P3"` | `chip--priority chip--p3` | `P3` | transparent | `--text-muted` | `--text-faint` |
| `state` | any ticket state / item status string | `chip--state` + `data-value` | value with `_`→space | `--surface-overlay` | `--text-default` (`done`/`dropped` → `--text-muted` via `[data-value]`) | transparent |
| `project` | project string | `chip--project` | value | transparent | `--text-muted` | `--text-faint` |
| `deadline` | ISO date | `chip--deadline` (+ `chip--overdue` when `opts.overdue`) | value | transparent / overdue: `--accent-surface` | `--text-muted` / overdue: `--accent-bright` | `--text-faint` / overdue: `--accent-bright` |
| `pending-proposal` | — | `chip--pending-proposal` | `proposal pending` | `--accent-surface` | `--accent-text` | `--accent-bright` |
| `running-claim` | — | `chip--running-claim` | `● running` | `--surface-overlay` | `--text-strong` | `--text-muted` |
| `blockers-cleared` | — | `chip--blockers-cleared` | `blockers cleared` | transparent | `--text-strong` | `--text-muted` |
| `auto-blocked` | — | `chip--auto-blocked` | `auto-blocked` | `--accent-surface` | `--accent-text` | `--accent-bright` |
| `frozen` | — | `chip--frozen` | `frozen` | transparent | `--text-faint` | `--text-faint` |

The mapping IS the one-accent rule made mechanical: accent styling appears exactly where a human is needed (P0, pending-proposal, auto-blocked, overdue); activity and good news stay achromatic at graded emphasis. Marker labels are fixed in a small internal map; `value` is ignored for markers.

### 5.6 `entityRow({title, href, chips}) -> HTMLAnchorElement` — D11 item 6

```
a.entity-row[href]            e.g. "#/ticket/t_abc"
├─ span.entity-row-title      textContent = title
└─ span.entity-row-chips      ← chip nodes appended
```
A native anchor: click-through is a hash change, refresh/middle-click/back all work — state lives in the URL only.

### 5.7 `errorLine(err) -> HTMLElement` — D11 item 17

```
div.error-line
├─ span.error-code       textContent = (err && err.code) || "error"
└─ span.error-message    textContent = (err && err.message) || "request failed"
```
Consumes exactly what `fetchJson` rejections carry (the PlannerError envelope passthrough). Rendered inline near the failed action by callers; never a toast/modal layer.

## 6. `assets/app.js` — router, registry, boot

Attaches `Planner.registerScreen`; owns the shell instance and boot. Internal state: `lastHash` (entrance detection only — the route itself is always re-derived from `location.hash`).

### Screen registry

- `Planner.registerScreen(name, render)` — `render(root, params)`; stored in an internal map. Overwrite-wins: T15–T17's scripts (which will be appended to the shell AFTER app.js) re-register the real screens over the placeholders before `DOMContentLoaded` fires. **This is the entire surface the router exposes** — no navigate(), no route events, no current-route getter; screens navigate by anchors and read `params`.
- T14 registers six placeholders: for each of `day review board ticket sprint backlog`, `render(root)` appends one `div.quiet-line` with textContent `not built yet`. One quiet line — not instructional.

### Router

`route()`:
1. `hash = location.hash`; if `""` or `"#"` → `location.replace(Planner.config.ROUTES.day)` and return (the rewrite fires `hashchange`, which re-enters — no double render).
2. `segments = hash.slice(1).split("/").filter(Boolean)` → `["day"]` … `["ticket","t_x"]`.
3. `name = segments[0]`; `params = {}`; `name === "ticket"` → `params.id = segments[1]` (missing id ⇒ unknown route).
4. Unknown name/shape → `shell.setActiveNav(null)`; content gets one `div.quiet-line` `no such screen`; return.
5. `shell.setActiveNav(name)`; `entrance = (hash !== lastHash)`; `lastHash = hash`.
6. `shell.content.replaceChildren(screenDiv)` where `screenDiv = div.screen` (+ class `screen-enter` only when `entrance`); call `registry[name].render(screenDiv, params)`.

Entrance class only on actual navigation: WS-flush re-renders (same hash) repaint without re-animating — motion marks arrival, not churn.

### Boot sequence (on `DOMContentLoaded`)

1. `shell = Planner.components.appShell()`; mount into `#app`.
2. `Planner.api.fetchJson(Planner.config.API.META)` → `debounceMs = meta.ui_debounce_ms`; on failure fall back to `DEBOUNCE_MS_DEFAULT` (one `console.debug`, boot continues — the server just served the page; a meta hiccup must not blank the app).
3. `Planner.bus.onInvalidate(onFlush)`; `Planner.bus.start(debounceMs)` — `onFlush` = `refreshBadge(); route();`. The bus has exactly ONE subscriber; "single notification to the active screen's refetch callback" is structural.
4. `window.addEventListener("hashchange", route)`; then `route()` (which performs the `#/day` default rewrite if needed).
5. `refreshBadge()`: `fetchJson(API.QUEUES)` → `shell.setReviewBadge(res.approvals.length)`; failure → one `console.debug`, badge untouched. Called at boot and on every flush (badge is shell chrome riding the same single flush — not a second subscriber).

Refresh-restores-state holds by construction: every render derives from `location.hash` + fresh fetches; zero state outlives the URL.

## 7. `src/planner/core/server.py` — the `_SHELL` rewrite (and nothing else)

Replace the `_SHELL` constant (currently line 45) with exactly:

```python
_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>planner</title>
<link rel="stylesheet" href="/assets/tokens.css">
<link rel="stylesheet" href="/assets/app.css">
</head>
<body>
<div id="app"></div>
<script src="/assets/config.js"></script>
<script src="/assets/api.js"></script>
<script src="/assets/markdown.js"></script>
<script src="/assets/components.js"></script>
<script src="/assets/app.js"></script>
</body>
</html>
"""
```

Dependency order: tokens before app.css (tokens must be defined when component rules resolve), config → api → markdown → components → app (each later file reads namespaces the earlier ones attached; app.js boots last). The `GET /` handler body (`return _SHELL`) is already correct and stays; **every other byte of server.py is untouched** — assert, lifespan, error handler, router loop, meta, events_ws, test router, StaticFiles mount.

## 8. `assets/app.css` — component styling, tokens only

Every rule uses `var(--token)` per §0.4. Selector inventory (must match §5's class names exactly):

- **Base**: `* { box-sizing: border-box }`; `body { margin: 0; background: var(--surface-base); color: var(--text-default); font-family: var(--font-ui); font-size: var(--type-md); line-height: 1.5; }`; `button, textarea { font: inherit; }`; `:focus-visible { outline: var(--border-strong) solid var(--accent-bright); outline-offset: var(--border-hairline); }`.
- **Shell**: `.shell { min-height: 100vh; }` · `.shell-nav { display: flex; align-items: center; gap: var(--space-5); padding: var(--space-3) var(--space-5); background: var(--surface-raised); border-bottom: var(--border-hairline) solid var(--surface-overlay); }` · `.shell-brand { font-size: var(--type-sm); font-weight: 600; color: var(--text-strong); }` · `.shell-links { display: flex; gap: var(--space-2); }` · `.nav-link { color: var(--text-muted); font-size: var(--type-sm); text-decoration: none; padding: var(--space-1) var(--space-2); border-radius: var(--radius-sm); transition: color var(--motion-fast) var(--motion-ease), background var(--motion-fast) var(--motion-ease); }` · `.nav-link:hover { color: var(--text-strong); }` · `.nav-link.active { color: var(--text-strong); background: var(--surface-overlay); }` · `.nav-badge { font-size: var(--type-xs); background: var(--accent-surface); color: var(--accent-text); border: var(--border-hairline) solid var(--accent-bright); border-radius: var(--radius-pill); padding: 0 var(--space-2); margin-left: var(--space-1); }` · `.nav-badge.hidden { display: none; }` · `.shell-content { padding: var(--space-5); }`.
- **Entrance**: `.screen-enter { animation: screen-enter var(--motion-slow) var(--motion-ease); }` with `@keyframes screen-enter { from { opacity: 0; transform: translateY(var(--space-2)); } to { opacity: 1; transform: none; } }` — the one animation; ease-out, no bounce.
- **Panel**: `.panel { background: var(--surface-raised); border: var(--border-hairline) solid var(--surface-overlay); border-radius: var(--radius-md); margin-bottom: var(--space-4); }` · `.panel-title { margin: 0; padding: var(--space-3) var(--space-4); font-size: var(--type-lg); font-weight: 600; color: var(--text-strong); border-bottom: var(--border-hairline) solid var(--surface-overlay); }` · `.panel-body { padding: var(--space-4); }`.
- **Markdown**: `.markdown p { margin: 0 0 var(--space-3); }` · `.markdown :last-child { margin-bottom: 0; }` · `.markdown h1 { font-size: var(--type-lg); }` · `.markdown h2 { font-size: var(--type-md); font-weight: 600; }` · `.markdown h3 { font-size: var(--type-sm); font-weight: 600; }` (all three: `color: var(--text-strong); margin: var(--space-4) 0 var(--space-2);`) · `.markdown code { font-family: var(--font-mono); font-size: var(--type-sm); background: var(--surface-sunken); padding: 0 var(--space-1); border-radius: var(--radius-sm); }` · `.markdown pre { background: var(--surface-sunken); padding: var(--space-3); border-radius: var(--radius-md); overflow-x: auto; }` · `.markdown pre code { background: none; padding: 0; }` · `.markdown ul, .markdown ol { margin: 0 0 var(--space-3); padding-left: var(--space-5); }` · `.markdown a { color: var(--accent-bright); }`.
- **Field editor**: `.field-editor { display: flex; flex-direction: column; gap: var(--space-2); }` · `.field-editor-input { width: 100%; background: var(--surface-sunken); color: var(--text-default); border: var(--border-hairline) solid var(--text-faint); border-radius: var(--radius-sm); padding: var(--space-2) var(--space-3); font-family: var(--font-mono); font-size: var(--type-sm); line-height: 1.5; resize: vertical; }` · `.field-editor-input:focus { border-color: var(--accent-bright); outline: none; }` · `.field-editor-actions { display: flex; align-items: center; justify-content: flex-end; gap: var(--space-2); }`.
- **Buttons** (shared, used by Save now, reused by screens later — not a new component, a style class): `.button { background: var(--surface-overlay); color: var(--text-strong); border: var(--border-hairline) solid var(--text-faint); border-radius: var(--radius-sm); padding: var(--space-1) var(--space-3); font-size: var(--type-sm); cursor: pointer; transition: background var(--motion-fast) var(--motion-ease), border-color var(--motion-fast) var(--motion-ease); }` · `.button--primary { background: var(--accent-surface); color: var(--accent-text); border-color: var(--accent-bright); }` · `.button:disabled { opacity: 0.5; cursor: default; }`.
- **Chips**: `.chip { display: inline-flex; align-items: center; gap: var(--space-1); font-size: var(--type-xs); font-weight: 500; padding: 0 var(--space-2); border-radius: var(--radius-pill); border: var(--border-hairline) solid transparent; background: var(--surface-overlay); color: var(--text-default); white-space: nowrap; line-height: 1.6; }` plus the variant rules exactly per the §5.5 table (`.chip--p0`…`.chip--p3`, `.chip--state[data-value="done"], .chip--state[data-value="dropped"] { color: var(--text-muted); }`, `.chip--project`, `.chip--deadline`, `.chip--overdue`, `.chip--pending-proposal`, `.chip--running-claim`, `.chip--blockers-cleared`, `.chip--auto-blocked`, `.chip--frozen`).
- **Entity row**: `.entity-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); text-decoration: none; color: var(--text-default); transition: background var(--motion-fast) var(--motion-ease); }` · `.entity-row:hover { background: var(--surface-overlay); }` · `.entity-row-title { font-size: var(--type-md); }` · `.entity-row:hover .entity-row-title { color: var(--text-strong); }` · `.entity-row-chips { display: flex; gap: var(--space-1); flex-shrink: 0; }`.
- **Error line**: `.error-line { display: flex; align-items: baseline; gap: var(--space-2); font-size: var(--type-sm); border-left: var(--border-strong) solid var(--accent-bright); padding-left: var(--space-2); }` · `.error-code { font-family: var(--font-mono); font-size: var(--type-xs); color: var(--accent-text); background: var(--accent-surface); padding: 0 var(--space-1); border-radius: var(--radius-sm); }` · `.error-message { color: var(--text-default); }`.
- **Quiet line** (empty states, placeholders, unknown route): `.quiet-line { color: var(--text-muted); font-size: var(--type-sm); }`.

No max-width/layout literals: the shell is fluid with token padding; content measure is a screen-level concern for T15–T17.

## 9. `orchestration/tickets/T14-ui-foundation/smoke.py` — the scripted gate

Follows the T10 smoke conventions (`ok NN` prints, `SMOKE PASS`, nonzero on any failure, full cleanup in `finally`), but boots **in-process uvicorn** and drives **real headless chromium** (Playwright 1.61.0 + chromium are installed in `.venv` — verified).

**Event-append endpoint choice: `POST /api/ideas`.** Justified from the code read: `sprints/api.py` `create_idea` → `_create_idea` (lines 80–94) performs exactly one `INSERT INTO ideas` plus exactly one `append_event(..., EventKind.idea_created, ...)` inside one `txn` — one HTTP call ⇒ exactly one new `events` row, no auth headers, no preconditions, no side-band events (unlike ticket creation, which also writes grant/state artifacts). On a fresh DB the event ids are deterministic: three idea posts ⇒ cursor 3.

Steps (numbered `ok` checks):

1. `REPO = Path(__file__).resolve().parents[3]`; `os.chdir(REPO)` — `create_app` mounts `StaticFiles(directory="assets")` cwd-relative.
2. Temp dir; pick a free port (T10's `free_or` helper). Build an **explicit env dict** (never mutate `os.environ`) and `config = load_config(path=None, env=env)` with: `PLAN_TEST_MODE=1`, `PLAN_FAKE_NOW=2026-07-04T12:00:00`, `PLAN_DB_PATH=<tmp>/planning.db`, `PLAN_PORT=<port>`, `PLAN_LOGS_DIR=<tmp>/logs`, `PLAN_DISPATCHER_LOCK_PATH=<tmp>/dispatcher.lock`, and `PLAN_WS_POLL_MS=50` (config.py:147 — tightens batch cadence so the coalescing proof is timing-robust; ui_debounce_ms stays the default 250 so check (b) asserts the real value).
3. Mirror `cli/main.py serve` wiring (lines 59–71): `connect` + `create_schema` on the temp DB; `build_clock(config)`; `build_adapters(config)` (test mode ⇒ fakes); `conn_factory` closure; `app = create_app(...)`.
4. `server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))`; run `server.run` in a daemon `threading.Thread` (uvicorn skips signal handlers off the main thread). Readiness: T10's `wait_ready` loop on `GET /api/meta`. → `ok` "server up".
5. **(a) Shell references, in order**: `GET /` → 200, `text/html`; walk `body.index(...)` cursor-style over `"/assets/tokens.css"`, `"/assets/app.css"`, `"/assets/config.js"`, `"/assets/api.js"`, `"/assets/markdown.js"`, `"/assets/components.js"`, `"/assets/app.js"` asserting strictly increasing positions (proves presence AND order: both CSS, then the five scripts). Then `GET /assets/<each of the 7>` → 200 (mount actually serves them; catches filename typos). → `ok`.
6. **(b) Meta keys the JS consumes**: `GET /api/meta` → `{"ui_debounce_ms": 250, "ws_poll_ms": 50, "test_mode": True}` — assert all three exactly. → `ok`.
7. Playwright boot: `sync_playwright()`, `chromium.launch()`, new page; register `page.on("console", ...)` collecting message texts; `page.goto(base + "/")`; `page.wait_for_function("() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1", timeout=15000)` — boot completed and WS open. Assert `page.evaluate("location.hash") == "#/day"` (default route rewrite) and `"not built yet" in page.inner_text("#app")` (placeholder wired through the router). → `ok` (shell + router + WS).
8. **(c1) Single event ⇒ exactly one debounced flush**: settle 0.5s; `f0 = page.evaluate("window.__plannerDebug.flushes")`; `POST /api/ideas {"title": "smoke bus proof 1"}` → 200, id starts `idea_`; `page.wait_for_function("f => window.__plannerDebug.flushes === f + 1", arg=f0, timeout=5000)`; then `time.sleep(1.0)` (> poll 50ms + debounce 250ms + margin) and assert `flushes == f0 + 1` **still** — exactly one callback, not zero, not N. Assert some captured console line contains `"[planner] flush"` (the sanctioned hook observed). → `ok`.
9. **(c2) Batch coalescing ⇒ still one flush**: `f1` = current flushes; two `POST /api/ideas` on adjacent lines (localhost, both land within the 250ms window even if they split across two 50ms-poll batches); wait for `flushes == f1 + 1`; `sleep(1.0)`; assert `flushes == f1 + 1` — two events, ONE debounced refetch. Assert `page.evaluate("window.__plannerDebug.cursor") == 3` (fresh DB: three idea_created rows ⇒ event ids 1..3 — cursor tracking proven). → `ok`.
10. Router nav: `page.goto(base + "/#/board")`; assert placeholder text present again. → `ok`.
11. Teardown in `finally`: close page/browser/playwright; `server.should_exit = True`; `thread.join(timeout=10)`; `shutil.rmtree(tmp, ignore_errors=True)`. `sys.exit(main())` pattern; any failed assert → traceback → exit 1; success prints `SMOKE PASS (n checks)`.

Ruff notes for the implementer: ≤100 cols, imports sorted (I), no unused (F), modern syntax (UP); module docstring stating the run command like T10's.

---

## 10. Execution order for the implementer

1. `assets/tokens.css` (final values, §1) — everything else consumes it.
2. `assets/config.js` (§2) → `node --check`.
3. `assets/api.js` (§3) → `node --check`.
4. `assets/markdown.js` (§4) → `node --check`.
5. `assets/components.js` (§5) → `node --check`.
6. `assets/app.js` (§6) → `node --check`.
7. `assets/app.css` (§8) — selectors against §5's finalized class names.
8. `src/planner/core/server.py` — swap `_SHELL` only (§7).
9. `orchestration/tickets/T14-ui-foundation/smoke.py` (§9).
10. Full self-check (§11); fix and repeat until all green.

## 11. Self-check list (run fresh, in order)

```sh
cd /Users/khushaljagota/.hermes/planning-v2
for f in assets/*.js; do node --check "$f" || echo "FAIL $f"; done
grep -nE '#[0-9a-fA-F]{3}|[0-9]+px|[0-9]+rem|[0-9]+em|[0-9]+ms|[0-9]+\.?[0-9]*s\b' assets/app.css   # expect NO output
grep -nE '\b(import|export)\b|await' assets/*.js                                                    # expect NO output
.venv/bin/ruff check .
.venv/bin/mypy src/
.venv/bin/pytest tests/unit -q
.venv/bin/python orchestration/tickets/T14-ui-foundation/smoke.py
```

Acceptance mapping: server boots + `/` renders shell with nav (smoke 5/7); placeholders wired through the router (smoke 7/10); WS connect + one debounced refetch per append, console-hook observable (smoke 8/9); `node --check` green (loop above); ruff/mypy unaffected (commands above); unit suite untouched-green.

## 12. Risks / notes for the orchestrator's sense-check

1. **Type tokens added** (`--type-xs…xl`, `--font-ui`, `--font-mono`) — the sanctioned evidence-of-need case; three-line argument in §1. If overruled, app.css cannot satisfy "zero literal sizes".
2. **Accent changed blue → amber** with a fixed meaning ("needs the human"); surfaces de-blued to keep the accent the only hue. Pure personality call, reversible by editing tokens.css alone — which is the design system working as specced.
3. **Nav set**: five links (Day, Review+badge, Board, Sprint, Backlog); Ticket routed but reached only via rows/cards since `#/ticket/<id>` needs an id. SPEC §10.3's "card click opens Ticket" supports this.
4. **`appShell` returns a handle object** (`{el, content, setReviewBadge, setActiveNav}`), not a bare node — the router needs the slot and setters; the other six components return plain nodes.
5. **Console hook shape**: `window.__plannerDebug = {flushes, wsOpens, cursor}` + exactly one `console.debug("[planner] flush n")` per bus flush and one per WS open. One deliberate extra global beside `Planner`; sanctioned by the ticket's "observable via console hook".
6. **Attention variants share one look**: P0, pending-proposal, auto-blocked, overdue all wear the accent (labels differentiate). Deliberate consequence of ONE accent — visual language stays honest instead of inventing semantic colors (which would be a new token category without sanction).
7. **Markdown: single newline → `<br>`** inside paragraphs (planner briefs/proposals are chat-adjacent text; strict-markdown space-joining would silently mangle them). Headings cap at `h3` (4+ `#` render as h3) to stay inside five type sizes.
8. **Unknown route** → one quiet line `no such screen` (no redirect — the URL stays honest). Empty markdown → `(none)`, matching the server's copy-text convention.
9. **Meta fetch failure** at boot falls back to the 250ms default from config.js and boots anyway — a meta hiccup must not blank an already-served page. Not speculative resilience: it is the ticket's stated purpose for the config.js default.
10. **Smoke tempo**: server runs with `PLAN_WS_POLL_MS=50` so the two-event coalescing proof has ~230ms of slack; `ui_debounce_ms` stays 250 (asserted). Residual flake risk only if two localhost POSTs straddle >200ms — accepted; if it ever flakes, the fallback is widening the sleep, not weakening the exactly-one assertion. Reconnect/backoff is implemented per §3 but not smoke-tested (needs a server bounce) — Codex review should eyeball that path.
11. **T15–T17 seam**: their `<script>` tags will be appended to `_SHELL` after `app.js`; classic scripts execute in order before `DOMContentLoaded`, so `registerScreen` overwrite-wins replaces the placeholders before boot routes. Router exposes `registerScreen(name, render(root, params))` and nothing else.
12. **Audio seam (§14)**: no chat code in T14; the bus and components are input-source-agnostic; nothing here assumes typed text anywhere. The chat panel (D11 item 10) lands later behind the gateway adapter.
