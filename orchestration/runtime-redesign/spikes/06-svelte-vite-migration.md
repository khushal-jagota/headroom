# Spike 06 — Svelte 5 + Vite migration (plan)

## 0. Current-state constraints

The migration is frontend-only. FastAPI, SQLite, existing domain APIs, and the typed event feed stay in place.

Current frontend behavior to replace:

- `assets/app.js` owns a hash router that creates a fresh `.screen` and calls the registered screen renderer on navigation or refresh (`assets/app.js:34-72`).
- Every WS flush refreshes the badge and calls `route()`, so any backend event rebuilds the active screen (`assets/app.js:85-88`).
- `assets/api.js` explicitly treats WS batches as generic invalidation: it reads `cursor`, ignores `events`, and schedules one flush (`assets/api.js:1-6`, `assets/api.js:115-128`).
- The backend already sends the event specificity we need: each event has `id`, `entity_id`, `kind`, `payload`, and `created_at` (`src/planner/core/ws.py:27-34`), batched as `{events, cursor}` (`src/planner/core/ws.py:60-62`).
- Chat currently survives whole-screen rebuilds through module-scope transcript/draft/pending objects (`assets/components.js:334-341`) and renders from those into `chatPanel` (`assets/components.js:832-840`).
- The ticket screen is the first pressure point: it fetches detail, sprints, chat status, and current sprint in parallel (`assets/screens-ticket.js:616-623`) and includes the chat rail (`assets/screens-ticket.js:570-576`).
- `assets/markdown.js` is the only HTML injector and owns the hardened `innerHTML` path (`assets/markdown.js:1-5`, `assets/markdown.js:175-177`).
- `assets/tokens.css` is the design authority and must remain the single source for color, type, radius, motion, spacing, and border tokens (`assets/tokens.css:1-5`).

The target is Svelte 5 runes + Vite, same-origin static assets served by FastAPI in production, with typed WS events driving targeted resource invalidation instead of route-level invalidation.

## 1. Build + serve setup

### Source layout

Add a frontend workspace at `web/`:

- `web/package.json`
- `web/vite.config.ts`
- `web/svelte.config.js`
- `web/tsconfig.json`
- `web/index.html`
- `web/src/main.ts`
- `web/src/App.svelte`
- `web/src/lib/api.ts`
- `web/src/lib/resources.ts`
- `web/src/lib/ws.ts`
- `web/src/lib/markdown.ts`
- `web/src/routes/*`
- `web/src/components/*`

Keep backend Python packaging unchanged. The frontend build is an extra deploy/build step, not a replacement for the Python app.

### CSS and markdown

Keep `assets/tokens.css` as the single token file. The Vite HTML should link it directly as `/assets/tokens.css`; do not copy token values into Svelte files.

During migration, continue serving `assets/app.css` for existing class names. New Svelte component CSS may be colocated, but it must consume `tokens.css` variables rather than hardcoding new design primitives. The final cleanup can either keep a global structural stylesheet or move structural CSS into Svelte components; `tokens.css` remains global either way.

Port `assets/markdown.js` as a thin module wrapper without changing behavior. The rule is: all rendered markdown HTML still passes through the same sanitizer/renderer logic, and Svelte components inject markdown only through a dedicated `MarkdownBlock` component.

### Vite production build

Configure Vite to build into a stable directory, for example `web/dist`, with `base: "/_app/"`.

FastAPI production serving:

- Mount the built Vite directory at `/_app`, so hashed JS/CSS chunks resolve as `/_app/assets/...`.
- Serve `web/dist/index.html` for `/` once cut over.
- Continue mounting `/assets` for `tokens.css`, legacy CSS during coexistence, and any temporary legacy assets.
- Continue mounting `/static` for favicon assets.

Today `src/planner/core/server.py` returns an inline shell that loads classic scripts (`src/planner/core/server.py:44-72`) and mounts `/assets` + `/static` (`src/planner/core/server.py:153-154`). The migration should replace the inline production shell with “read built `index.html` if present,” while keeping a legacy shell available during coexistence.

### Development story

Use Vite dev server for frontend development:

- Run FastAPI normally with `plan serve`.
- Run Vite from `web/`.
- Vite proxies `/api`, `/api/events` with `ws: true`, `/assets`, and `/static` to FastAPI.
- Browser opens `http://127.0.0.1:5173/`; all API paths remain relative and same-origin from the app’s perspective.

Do not add CORS. The dev proxy preserves the production same-origin model.

### VPS deployable layout

Deploy process:

1. Install Python dependencies.
2. Install frontend dependencies with the lockfile.
3. Run `npm --prefix web ci`.
4. Run `npm --prefix web run build`.
5. Start `plan serve` under the VPS process manager from the repo root or installed app directory.
6. FastAPI serves `/`, `/_app/*`, `/assets/tokens.css`, `/static/*`, and `/api/*` from one origin.

Note: once this is network-reachable on a VPS, auth becomes required. Auth is explicitly out of scope for this migration plan.

### `./verify` integration

`./verify` currently runs Python checks, unit tests, a build check over `src/` and every file under `assets/`, then e2e (`scripts/verify.py:1-7`, `scripts/verify.py:85-110`, `scripts/verify.py:140-143`). Extend it so frontend build happens before e2e:

- Add a frontend gate: `npm --prefix web ci` only in CI/deploy if dependencies are not installed, then `npm --prefix web run check` and `npm --prefix web run build`.
- Keep Python gates first.
- Run e2e against the built app, not the Vite dev server.

The e2e harness currently asserts `/assets/api.js` exists (`tests/e2e/conftest.py:126-128`) and waits on `window.__plannerDebug.wsOpens` / `flushes` (`tests/e2e/conftest.py:181-193`). During migration, keep a compatible `window.__plannerDebug` object in the new WS client. At cutover, update the readiness assertion from `/assets/api.js` to the Vite app entry or root shell marker.

## 2. Data layer: resource cache + typed WS invalidation

This is the core of the migration.

### Goal

Replace “WS event → route() → full-screen rebuild” with:

`WS event batch → map event(s) to resource keys → mark only those keys stale → subscribed Svelte components refetch or update`

The backend event feed already has the needed shape. `EventKind` is centralized in `src/planner/core/contracts.py:38-77`.

### Resource key scheme

Use stable string keys. Initial keys:

- `meta`
- `queues`
- `board`
- `day:today`
- `day:<yyyy-mm-dd>`
- `sprints`
- `sprint:current`
- `sprint:<id>`
- `items:backlog`
- `item:<id>`
- `ideas`
- `ticket:<id>`
- `ticket-events:<id>`
- `chat-status:<entity_id>`
- `chat-commands`

Aggregate keys should be intentionally conservative. Over-invalidating `board` or `sprint:current` is acceptable; re-rendering the whole active route is not.

### Public API

Create `web/src/lib/resources.ts` with a small cache API:

- `resource<T>(key, fetcher, options?)`: returns a Svelte-rune-backed resource handle.
- `peek<T>(key)`: reads current cached data without subscribing.
- `invalidate(key, reason?)`: marks a key stale and refetches if it has subscribers.
- `invalidateMany(keys, reason?)`: batch invalidation.
- `refresh(key)`: force refetch.
- `mutateJson(path, options, expectedInvalidations?)`: wrapper for writes; performs the request and optionally marks expected keys stale while waiting for WS confirmation.
- `startEventStream(options)`: opens `/api/events?since=<cursor>`, parses typed events, updates cursor, and dispatches invalidations.
- `stopEventStream()`: test/dev teardown.

A resource handle exposes:

- `data`
- `error`
- `loading`
- `stale`
- `refresh()`
- `invalidate()`

In Svelte, usage should look conceptually like:

- Ticket route creates `ticket = resource("ticket:" + id, () => getJson("/api/tickets/" + id))`.
- Template reads `ticket.data`, `ticket.loading`, and `ticket.error`.
- On save, call `mutateJson("/api/tickets/" + id, {method: "PATCH", body})`.
- When a WS event invalidates `ticket:<id>`, only components subscribed to that ticket refresh.

### Fetch rules

- Deduplicate concurrent fetches for the same key.
- Abort or ignore stale responses when a newer fetch supersedes them.
- Keep last good data during background refresh unless the resource has never loaded.
- Preserve structured errors from the existing `fetchJson` behavior (`assets/api.js:35-78`).
- Default writes remain non-optimistic. The current UI discipline is “successful save appears after WS/refetch,” documented repeatedly in screens such as ticket (`assets/screens-ticket.js:10-11`) and day (`assets/screens-day.js:13-17`). The new cache can retire the “no client state store” doctrine, but it should not silently introduce optimistic canonical data.

### Event-to-key mapping

**Decision A (settled).** Propagation is `events → keyed invalidation → targeted refetch`; Svelte runes do the render-granularity half. Reactive queries (option B) were weighed against this and rejected: for a single-user local app the reactive-query runtime is overbuilt, and it does not remove the dependency-mapping burden for our aggregate views (board, queues, current sprint) — it just relocates it server-side with more moving parts. See the independent decision in `decisions.md`.

The one live risk of A is rot: a new event kind or resource added without wiring its invalidation, silently leaving the UI stale. The mapping below is structured specifically so that cannot happen quietly.

Mapper: `keysForEvent(event): string[]`, kept in one file.

**Primary rule — the `entity_id` prefix, and it must be complete on its own.** Every event carries an `entity_id`; its prefix names the entity type, and the entity type fixes the resource plus the aggregates that entity appears in. This rule alone fully covers the normal case, so **adding a new event kind about an existing entity needs no mapping change** — the prefix already routes it. Fold every "aggregate also touched" into the prefix set here; do not restate it per kind.

- `t_*` (ticket) → `ticket:<id>`, `board`, `queues`, `sprint:current`
- `i_*` (sprint item) → `item:<id>`, `items:backlog`, `sprint:current`, `board`, `queues`
- `s_*` (sprint) → `sprint:<id>`, `sprint:current`, `sprints`
- `day_*` → `day:<date>` (and `day:today` when the date is today's planning date / the today alias)
- `idea_*` → `ideas`

Over-invalidating an aggregate (`board`, `sprint:current`) is fine; re-rendering a whole route is not.

**Kind-specific entries exist ONLY for events the prefix rule cannot express** — never to repeat what the prefix already covers:

- `link_added` / `link_removed`: the change touches two entities named in the payload, not just `entity_id` — invalidate `ticket:`/`item:` for *both* endpoints, plus `board`, `queues`, `sprint:current`.
- `chat_session_created`: invalidate the chattable entity's resource, but never the local chat transcript.
- `day_*` carrying a payload `ticket_id`: also invalidate that `ticket:<id>` and `board`.

If a new event's correct invalidation *is* its entity's prefix set, add nothing. Add a kind-specific entry only when the prefix set is genuinely wrong or incomplete for that event, and state why.

**Anti-rot backstop — a completeness test.** A unit test iterates every event kind the backend can emit (the canonical event-kind list) and asserts `keysForEvent` returns at least one key for each; it also rejects any `entity_id` whose prefix is unknown to the primary rule. A new backend event kind that maps to nothing — or a new entity prefix nobody taught the mapper — fails `./verify` rather than going silently stale. This test plus the canonical event-kind list are the single forcing function; keep them in sync. This is a required item of Phase 2, not optional.

## 3. Streaming chat

### Recommendation: SSE over a dedicated chat stream endpoint

Use an SSE-style streaming HTTP response, consumed with `fetch()` and a `ReadableStream`, not browser `EventSource`. This allows a `POST` body with the user text and avoids a two-step “create stream then GET” flow.

Prefer SSE/fetch over a chat WebSocket because:

- The chat stream is one-way after submit: client sends text once, server streams model output.
- The existing `/api/events` WebSocket remains dedicated to typed database invalidation.
- FastAPI can implement this with `StreamingResponse`.
- Error and completion semantics are simpler than multiplexing another bidirectional channel.

### Endpoint contract

Add:

- `POST /api/chat/{entity_id}/stream`
- Request body: `{ "text": string, "mode": "message" | "command" }`
- Response: `text/event-stream`

Events:

- `message_start`: `{ "entity_id": string, "mode": "message" | "command" }`
- `token`: `{ "text": string }`
- `message_done`: `{ "reply_text": string, "session_key": string, "kind": "assistant" | "system" }`
- `error`: `{ "code": string, "message": string, "detail": object }`

The stream should end after `message_done` or `error`.

### Backend mapping

Current chat API is request/response: `POST /api/chat/{entity_id}/send` calls `service.send()` and returns the completed reply (`src/planner/chat/api.py:42-58`). `RealGatewayAdapter.send()` resumes or creates a gateway session, submits a prompt, then drains child events until `message.complete` (`src/planner/core/adapters/real.py:140-169`). Command execution has the same drain-to-complete shape (`src/planner/core/adapters/real.py:197-241`, `src/planner/core/adapters/real.py:271-291`).

Add a streaming gateway surface alongside the existing methods:

- Reuse `_resume_or_create`.
- Submit the prompt.
- Yield gateway delta events as `token` when available.
- On `message.complete`, persist the session key using the same `_persist_key` logic in `chat/service.py:69-112`, emit `message_done`, and close.
- If the current stdio gateway only exposes `message.complete`, keep the streaming contract stable by emitting one `token` with the completed text before `message_done`. When gateway delta events become available, the frontend does not change.

The existing non-streaming endpoints can stay during migration for fallback and tests.

### Svelte chat component

Port chat as a persistent Svelte component:

- Owns transcript, draft, pending state, and stream controller locally.
- Appends the user message immediately as local UI state.
- Appends assistant tokens into the currently open assistant message.
- Does not rebuild transcript from a route render.
- Does not store transcript in the resource cache; transcript is still transient and may be lost on reload.
- Uses `chat-status:<entity_id>` only for gateway availability.
- Uses `chat-commands` for the slash menu, preserving the existing cached command catalog behavior (`assets/components.js:395-410`).

This removes the module-scope transcript survival hack from `assets/components.js:334-341`.

## 4. Component and screen port order

### Component mapping

Map current DOM-builder primitives to Svelte components first where they are shared:

- `appShell()` → `Shell.svelte`
- `markdownBlock()` → `MarkdownBlock.svelte`
- `fieldEditor()` / `inlineEdit()` → `InlineEdit.svelte`
- `chip()` → `Chip.svelte`
- `entityRow()` → `EntityRow.svelte`
- `errorLine()` → `ErrorLine.svelte`
- `proposalCard()` / `approvalBlock()` → `ProposalCard.svelte` / `ApprovalBlock.svelte`
- `chatPanel()` + input source → `ChatPanel.svelte` + `ChatComposer.svelte`
- Backlog/ideas segmented toggles → `SegmentedControl.svelte`
- Sprint/details disclosures → local route components unless reuse becomes obvious

Do not invent a design system beyond the existing primitives. The current components file is deliberately primitive-driven (`assets/components.js:1-4`); preserve that shape.

### Route model

Keep hash route shapes initially:

- `#/day`
- `#/review`
- `#/board`
- `#/ticket/<id>`
- `#/sprint`
- `#/sprint/tracking`
- `#/sprint/overview`
- `#/backlog`
- `#/ideas`

A tiny Svelte route parser is enough. Do not add a routing framework unless the route surface grows.

### Port order

1. **Ticket**
   - Highest value because it combines parallel resources, inline writes, proposals, scope controls, and chat.
   - Replace four parallel raw fetches with subscribed resources: `ticket:<id>`, `sprints`, `chat-status:<id>`, `sprint:current`.
   - Prove chat streaming survives unrelated ticket invalidations.
   - Preserve e2e selectors such as `data-screen="ticket"`, `data-ticket-id`, `data-field`, `data-chat`.

2. **Review**
   - Uses `queues` plus per-entry ticket/item detail.
   - Keep transient skip memory local to the route. Current skip state is module-scope and intentionally per-session (`assets/screens-review.js:12-14`); in Svelte it becomes route-local state.
   - Badge and review screen should subscribe to the same `queues` resource.

3. **Board**
   - Simple aggregate read of `/api/board` (`assets/screens-board.js:52-84`).
   - Good proof that board invalidates without changing the current route.

4. **Day**
   - Simple `day:today` resource over `/api/day/today` (`assets/screens-day.js:73-82`).
   - Preserve inline-edit markdown behavior.

5. **Sprint**
   - `sprint:current` powers both tracking and overview (`assets/screens-sprint.js:399-417`).
   - Keep the two hash subroutes.
   - Preserve native `<details>` disclosure behavior and existing selectors.

6. **Backlog**
   - `items:backlog` over `/api/items?sprint_id=null` (`assets/screens-backlog.js:29`, `assets/screens-backlog.js:272-281`).
   - Create form remains non-optimistic; successful create appears after cache invalidation.

7. **Ideas**
   - `ideas` over `/api/ideas` (`assets/screens-ideas.js:26-27`, `assets/screens-ideas.js:252-261`).
   - Capture form remains local state; list refresh comes from invalidation.

## 5. Coexistence and cutover

### Coexistence approach

Run old and new frontends side by side during migration:

- Keep the legacy no-build app available at `/legacy`.
- Serve the Svelte app at `/ui` during migration.
- Vite dev server serves the Svelte app in development.
- Production FastAPI can serve both `web/dist` and the legacy shell until cutover.

This avoids embedding Svelte inside the legacy `route()` loop, which would immediately reintroduce the global rebuild problem.

During migration:

- Ported screens exist in `/ui#/<route>`.
- Unported screens remain usable in `/legacy#/<route>`.
- The Svelte shell can link unported nav items to `/legacy#/...` until each route is ported.
- E2E tests move screen by screen from legacy root to `/ui`.

### Final cutover

When all screens are ported and e2e is green against the built Svelte app:

1. Change `/` to serve the Vite `index.html`.
2. Keep `/legacy` for one release only if needed.
3. Remove legacy script loading from the production shell.
4. Delete `assets/app.js` and the `Planner.bus` route invalidation model.
5. Delete legacy `screens-*.js` after their Svelte equivalents and tests are complete.
6. Keep `assets/tokens.css`.
7. Keep or port `assets/markdown.js` only as the single markdown renderer; do not create a second sanitizer.

The cutover is complete only when no backend event can call route-level re-rendering.

## 6. Phased, gated commits

Each phase should be independently reviewable. Run `./verify` green where feasible; when a phase cannot be fully green because e2e still targets legacy assumptions, update the test harness in the same phase.

### Phase 1 — Vite/Svelte scaffold and FastAPI static wiring

Deliver:

- `web/` Svelte 5 + Vite scaffold.
- Vite dev proxy for `/api`, `/api/events`, `/assets`, `/static`.
- FastAPI support for serving `web/dist` at `/ui` and `/_app`.
- `./verify` frontend build gate before e2e.
- Compatibility `window.__plannerDebug` shape in the new app, even before screens are ported.

Prove:

- `npm --prefix web run build` produces deployable assets.
- `plan serve` can serve legacy root and Svelte `/ui`.
- Existing legacy e2e remains green.
- Verify no production CORS requirement.

### Phase 2 — Resource cache and typed WS client

Deliver:

- `api.ts`, `resources.ts`, `ws.ts`.
- Event mapper with tests covering every `EventKind`.
- Cursor persistence for the current page lifetime.
- Debug counters compatible with e2e.
- No Svelte route depends on route-level invalidation.

Prove:

- Synthetic WS events invalidate expected keys.
- Unrelated keys do not refetch.
- Reconnect resumes with `since=<cursor>`, matching the existing cursor behavior (`assets/api.js:84-88`, `assets/api.js:106-128`).
- Aggregate invalidation is conservative but bounded.

### Phase 3 — Streaming chat backend and component

Deliver:

- Streaming chat service/adapter method.
- `POST /api/chat/{entity_id}/stream`.
- Fake gateway streaming test support.
- `ChatPanel.svelte` and `ChatComposer.svelte`.

Prove:

- Token events append without replacing the ticket DOM.
- `message.complete` still persists session keys through the existing chat session logic.
- Gateway error maps to an `error` stream event with structured error shape.
- Existing non-streaming chat endpoints still pass until callers are migrated.

### Phase 4 — Ticket route

Deliver:

- Svelte ticket route under `/ui#/ticket/<id>`.
- Svelte equivalents for ticket header, recap, approval, field sections, scope controls, copy, and chat rail.
- Resource subscriptions for `ticket:<id>`, `sprints`, `sprint:current`, `chat-status:<id>`.
- Ticket e2e moved or duplicated against `/ui`.

Prove:

- Editing a field invalidates and refreshes only relevant resources.
- A board-impacting ticket event updates `board` only if board is mounted/subscribed.
- Chat streaming continues through unrelated ticket updates.
- Existing selectors used by Playwright remain stable.

### Phase 5 — Review and board

Deliver:

- Review route using `queues` and detail resources.
- Board route using `board`.
- Shared nav badge from `queues`.

Prove:

- Approval accept/approve updates `queues`, review card, badge, ticket, and board without route rebuild.
- Skip memory remains local and resets on reload.
- Board updates from ticket events while preserving shell and route state.

### Phase 6 — Day, sprint, backlog, ideas

Deliver:

- Day route using `day:today`.
- Sprint route using `sprint:current` plus subroute state.
- Backlog route using `items:backlog`.
- Ideas route using `ideas`.

Prove:

- Inline day/sprint edits refresh their own resources only.
- Backlog create invalidates backlog and relevant sprint/item aggregates.
- Idea capture invalidates ideas only.
- No hardcoded design values bypass `tokens.css`.

### Phase 7 — Root cutover and legacy deletion

Deliver:

- `/` serves built Svelte app.
- `/legacy` removed or retained behind an explicit temporary flag.
- Legacy route invalidation deleted.
- Obsolete no-build screen files removed after e2e coverage is moved.
- Server shell no longer embeds classic script tags.

Prove:

- `./verify` runs build-before-e2e and passes against the Svelte app.
- No e2e waits depend on `/assets/api.js`.
- No code path calls `route()` in response to WS invalidation.
- VPS deploy from clean checkout produces a working same-origin app.

## 7. Risks and mitigations

- **Build/e2e interplay:** e2e currently assumes legacy assets and debug counters. Mitigate by adding the frontend build gate early and keeping `window.__plannerDebug` compatible until tests are updated.
- **Streaming endpoint reality:** the gateway currently drains to `message.complete`. Mitigate with a stable SSE contract that can emit one final token now and true deltas later.
- **Event mapping drift:** backend event kinds may gain payloads or new meanings. Mitigate with an exhaustive mapper test over `EventKind`.
- **Over-invalidation of aggregates:** broad invalidation of `board`, `queues`, or `sprint:current` can cause extra fetches. Accept this initially; optimize only after correctness.
- **Retiring the no-store doctrine:** the app will now have a canonical frontend cache. Keep it small, resource-keyed, non-optimistic by default, and fed by typed WS invalidation.
- **Two frontends during coexistence:** avoid sharing one route loop. Keep legacy at `/legacy` and Svelte at `/ui` until final cutover.
- **CSS fragmentation:** Svelte scoped styles can drift. Enforce `tokens.css` variables as the only design primitives.
- **Markdown safety regression:** do not use Svelte `{@html}` except inside the dedicated markdown component backed by the existing renderer logic.
