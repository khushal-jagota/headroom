# Live-update swap — implementation plan

Branch `simplify/live-update`. Replaces the entity event log + envelope push + event-kind→resource
mapping with: one commit-time contentless change signal, an SSE endpoint, and TanStack Svelte Query
in the browser. The bulk of the package is deletion.

## Today's mechanism (surveyed)

- Writers call `append_event` (`core/events.py`) inside their `BEGIN IMMEDIATE` transactions;
  ~60 call sites across `tickets/data.py`, `days/`, `sprints/`, `projects/`, `core/links.py`,
  `worker_settings/api.py`, `runtime/employee_step_repository.py`, `tickets/conversation_projection.py`,
  `conversation/sqlite_binding_repository.py`, `seed/importer.py`.
- `core/ws.py` tails the `events` table over `WS /api/events?since=`; `web/src/lib/ws.ts` receives
  batches, maps kinds→resource keys via `web/src/lib/resourceCatalogue.ts`, invalidates the keyed
  cache in `web/src/lib/resources.svelte.ts` (debounced by `/api/meta.ui_debounce_ms`).
- Completeness test `tests/unit/test_frontend_event_mapping.py` asserts every `EventKind` maps.
- Eligibility wake: `runtime/automatic_employee_step_eligibility_wake.py` (Loop/NoOp pair) is
  threaded as an explicit parameter through `tickets/actions.py` (14 call sites), `days/actions.py`,
  both API dependency wirings, `EmployeeStepRunner`, and `core/loops.py`. The discovery loop's
  `wake()` just sets a `threading.Event`; the periodic timer is the backstop.
- Event-history readers besides the tailer: `review_view` (`tickets/views.py`) computes
  `waiting_since` for needs_user tickets from `MAX(events.created_at)`; `GET /api/tickets/{id}/events`
  + `panels ticket events` print the raw log (id, kind, created_at — nothing else).
- Transactions end with literal `conn.execute("COMMIT")` everywhere except one `conn.commit()`
  (`worker_context/service.py`). All server-process connections are made by `core/db.connect`
  (API `conn_factory`, runtime threads, conversation repositories). Conversation writes are
  lifecycle-frequency (bindings, projections), not per-chunk — commit volume is modest.

## The new mechanism

### 1. Change signal hub — `src/planner/core/change_signal.py` (new, ~40 lines)

Process-wide, module-level hub: `subscribe(callback) -> unsubscribe-callable`, `emit()`.
Lock-protected subscriber list; `emit()` calls each subscriber, swallowing exceptions (the signal
is best-effort by design; SQLite plus the periodic timer stay canonical). Subscribers must be
cheap and thread-safe (`threading.Event.set`, `loop.call_soon_threadsafe`). No payload, no
vocabulary, no queue.

### 2. The write door — commit detection in `core/db.connect`

`db.connect` passes `factory=` a `sqlite3.Connection` subclass. It emits `change_signal.emit()`
exactly when a call closes an open transaction as a commit:

- override `execute()`: if the connection was `in_transaction` before the statement and is not
  after, and the statement is `COMMIT` (the codebase's uniform transaction end), emit after the
  statement returns (write lock already released);
- override `commit()`: if `in_transaction` was true, emit after the super call.

Rollbacks never emit. Every writer in the server process goes through `db.connect`
(HTTP endpoints, runner, discovery loop, gateway, conversation repositories), so this is the single
door; no call-site participation. Non-server processes using `db.connect` (seed importer, scripts)
emit into an empty hub — harmless; browser focus-refetch and the polling timer cover cross-process
writes, per the ruling.

Rejected alternatives: per-call-site emits (recreates the scattered-wake problem); emitting at HTTP
request teardown (misses runtime-thread writes).

Known exemption (recorded, deliberate): `conversation/employee_configuration.py` opens a raw
`sqlite3.connect` for its catalog cache and commits outside the door. Its writes affect neither
browser resources nor eligibility, and conversation/ is out of scope — it stays unobserved.

Door tests (ticket A): exactly one emission after `execute("COMMIT")` closing a transaction;
exactly one after `commit()` closing a transaction; none on rollback; none when the commit itself
fails; emission ordering after the lock is released; cross-thread emit reaching a subscriber.

### 3. SSE endpoint — `GET /api/changes` in `server.py` (+ small helper module `core/sse.py` if cleaner)

`StreamingResponse`, `media_type="text/event-stream"`. Per connection: subscribe a callback that
`call_soon_threadsafe`-sets an `asyncio.Event`; loop: wait for the event with `sse_heartbeat_ms`
timeout; on signal → clear + send `data: change\n\n` (multiple emits while waiting coalesce into
one frame); on timeout → send a comment line `: keep-alive\n\n` (invisible to EventSource, keeps
intermediaries alive, and a dead client makes the write fail so the generator exits).
`finally:` unsubscribes — on client disconnect and on server shutdown (task cancellation), so
shutdown is never held open (parity with the old tailer's guarantee; `test_server_shutdown_process`
adapts).

Shutdown (review finding, verified): uvicorn drains open connections BEFORE lifespan shutdown and
never force-closes an in-flight h11 streaming response, so an open SSE stream would hold SIGTERM
shutdown open indefinitely (the old WS avoided this with its disconnect watcher; uvicorn closes
websockets on shutdown, not HTTP streams). Fix: the SSE module keeps a registry of active streams,
each registering a thread-safe closer (`loop.call_soon_threadsafe` on a per-stream closing event);
`server_lifecycle/application.py` switches from `uvicorn.run(app, ...)` to an explicit
`uvicorn.Config` + a small `uvicorn.Server` subclass whose `handle_exit` closes all active SSE
streams before delegating to uvicorn's normal exit. This is the only launch path (`panels serve` →
supervisor → application.py child). Named test: a process-level test (pattern of
`test_server_shutdown_process.py`) sends SIGTERM while `/api/changes` is held open and asserts exit
within the grace budget.

SSE endpoint tests (ticket A): frame format (`data: change`), heartbeat comment cadence, coalescing
(N emits while parked → one frame), client-disconnect unsubscribes from the hub, shutdown closes
the stream.

Config: new `sse_heartbeat_ms` (`PLAN_SSE_HEARTBEAT_MS`, default 15000).
Deleted: `ws_poll_ms`, `ws_heartbeat_ms`, `ui_debounce_ms`, `events_read_limit` (config fields, env
vars, `config.yaml` entries, and any forwarding in `environments/logic/launch_env.py` /
`release_launcher.py`). `/api/meta` keeps only `test_mode` and `release_sha`.

### 4. Wake rewiring — subscription replaces parameter-threading

In `core/loops.py`, when the discovery loop starts, `change_signal.subscribe(loop.wake)`; the
unsubscribe runs in `BackgroundLoops.stop`. Over-waking is deliberate and cheap: the loop re-checks
the complete eligibility decision; a no-change check is read-only so no feedback loop.

Deleted end to end: `runtime/automatic_employee_step_eligibility_wake.py` (protocol + Loop/NoOp),
the `automatic_employee_step_eligibility_wake` parameters and `.wake()` calls in
`tickets/actions.py`, `days/actions.py`, the API dependency types in `tickets/api.py` /
`days/api.py`, the `EmployeeStepRunner` constructor parameter and its internal wake call
(runner commits now signal via the door), `server.py` / `core/loops.py` wiring and `app.state`
entry, `runtime/__init__.py` exports.

Actions that become pure pass-throughs once the wake parameter is gone (`delete_ticket`,
`file_proposal`, `file_current_proposal_with_recap`, `accept_proposal`, `edit_field_value`,
`change_scope`, `set_stage`, `drop_ticket`, `request_user_help`, `take_over_ticket`,
`release_ticket`, `set_stage_ownership`, `reconcile_ticket_from_external_work`) are collapsed:
callers call the `tickets_data` function directly. `resolve_creation_placement`, `create_ticket`,
`create_ticket_from_external_work`, `add_link`, `remove_link`, `return_ticket_for_revision` keep
real coordination and stay. Same judgment for `days/actions.py`.

### 5. Deletions

- `core/events.py` (append_event, delete_entity_history, read_events_since) and every call site —
  writers just write. Includes the one call in `conversation/sqlite_binding_repository.py`
  (mechanical fallout only; no other conversation change) and `tickets/conversation_projection.py`.
- `core/ws.py` + `WS /api/events` endpoint.
- `EventRow` in `core/contracts.py`; sweep `tests/typing/` fixtures.
- **`EventKind` is NOT deleted** (review finding, verified): `Decision`/`EventSpec`
  (`tickets/logic/decisions.py`) are the resolution engine's output contract, and the canonical
  write path branches on spec kinds — `stage_changed` triggers sprint-item child settlement
  (`tickets/data.py:358`), `proposal_filed` selects `awaiting_approval` (`data.py:~1341/1400`).
  The engine contract stays untouched; only the `append_event` persistence of each spec dies.
  Docstrings saying "events to append" update to say the specs are the engine's statement of what
  the write does. Kinds left with no consumer after the swap are a recorded carry-forward for a
  later resolution-engine cleanup, not this package.
- `GET /api/tickets/{id}/events`, `views.list_events_for_entity`, `event_json`.
- CLI `panels ticket events` — retired. Its output was exactly the raw log rows; nothing consumed
  a stored fact through it. (Recorded choice: retire, not convert.)
- `tests/unit/test_server_events.py`, `tests/unit/test_frontend_event_mapping.py`,
  `tests/unit/test_automatic_employee_step_eligibility_wake.py`; event-sequence assertions across
  the unit suite (~25 files reference events/wake — assertions on canonical rows stay, assertions
  on event rows/kinds go; wake-wiring tests become change-signal tests). The transport coverage
  the deleted `test_server_events.py` carried is replaced by the door + SSE tests named above.
- `web/src/lib/ws.ts`, `resources.svelte.ts`, `resourceCatalogue.ts`, and web tests
  `resource-cache`, `resource-catalogue`, `ws-connection`.

Surviving consumers of deleted surfaces, enumerated (review finding — each has a named owner):
- `tests/e2e/test_acp_conversation.py:1860` inserts into `events` directly — ticket C.
- `tests/e2e/test_cli_verbs.py:198` runs `panels ticket events` — ticket C.
- `tests/e2e/test_server_lifecycle.py:218` asserts `ui_debounce_ms` in `/api/meta` — ticket C.
- `tests/e2e/conftest.py:209-219` gates on `__plannerDebug.wsOpens` / `.flushes` as harness sync
  primitives — ticket B keeps a minimal `window.__plannerDebug` (`sseOpens`, invalidation flush
  count); ticket C rewrites the gates (the `settled` catch-up-replay gate dies with the replay —
  initial fetches are always current; only the "stream open before observed mutation" gate stays).
- `web/tests/vps-status.test.mjs:6` reads `resourceCatalogue.ts`; `acp-production-mount.test.mjs:26`
  asserts the old `/api/meta` fetch — ticket B.
- `src/planner/skills/panels-ticket-management/SKILL.md:135,203` tells agents to inspect ticket
  events/history — ticket C rewrites those instructions to canonical state (fields, recap, status).

### 6. Migration (third revision, after `ticket_status_reshape`)

One revision:
1. `ALTER TABLE tickets ADD COLUMN ticket_status_changed_at INTEGER NOT NULL DEFAULT 0`.
2. Backfill: per ticket, the `created_at` of its latest `events` row with
   `kind='ticket_status_changed'`; tickets with none get `updated_at`. (Conservative reading of
   the ruling's "backfill from updated_at or similar".)
3. `DROP INDEX idx_events_entity; DROP TABLE events`. Trivial drop — no table rebuild.

`db.py`'s `PRE_ALEMBIC_TABLE_NAMES` / `PRE_ALEMBIC_INDEX_NAMES` are frozen adoption
constants and deliberately keep `events` — do not touch.

Writers: the single canonical `ticket_status` write door in `tickets/data.py` sets
`ticket_status_changed_at = now` whenever the status value changes; BOTH ticket INSERT sites
(`tickets/data.py:~795` and `~960`) set it to the creation time — missing either leaves the
DEFAULT 0. (Verify by grep there is exactly one `UPDATE ... ticket_status` site.)

`review_view.user_help_requests` becomes a plain read of `ticket_status_changed_at` for
day-scoped needs_user tickets — the events join dies. Semantics preserved: for a ticket currently
in needs_user, "when status last changed" is "when it entered needs_user".

Migration test (named gate): build a pre-swap DB at the previous revision (existing pattern in
`tests/unit/test_db.py`), insert tickets + events rows including a needs_user ticket carrying
SEVERAL historical `ticket_status_changed` events (older transitions through other statuses, the
newest into needs_user) so the backfill provably takes the latest transition, plus a ticket with no
status events (updated_at fallback). Upgrade to head, assert: events table and index gone,
backfilled timestamps correct for both cases, review view serves the value.

### 7. Frontend — TanStack Svelte Query

- Dependency: `@tanstack/svelte-query` v6 (current package for Svelte 5; 6.1.x, peer dep
  `svelte ^5.25.0` — the lockfile already resolves svelte 5.56.4, so no svelte bump; commit
  `package-lock.json`). v6 is runes-based: thunked options, direct result properties.
- One `QueryClient` created in a plain module (the SSE client imports the same instance to call
  `invalidateQueries`), provided at the root ABOVE `App.svelte`'s own queries — either a provider
  wrapper mounted from `main.ts` or by passing the explicit client to App's own `createQuery`
  calls (App itself consumes the review query today; context set inside App does not reliably
  serve App's own script). Defaults: structural sharing on, refetchOnWindowFocus,
  refetchOnReconnect, staleTime 0.
- Query catalogue module: typed query-options creators keyed exactly like today's identities —
  `['board']`, `['review']`, `['day','today']`, `['items','backlog']`, `['ideas']`, `['projects']`,
  `['sprints']`, `['sprint','current']`, `['ticket', id]`, `['worker-types']`, `['workers']`,
  `['worker', type]`, `['skills-home']` — each `queryFn` a `fetchJson` of the same path.
- SSE client module (replaces `ws.ts`): `new EventSource('/api/changes')`;
  `onmessage` → debounced (250 ms trailing, client constant — parity with today's default)
  `queryClient.invalidateQueries()` — invalidate everything; only mounted queries refetch;
  `onopen` → status `connected` + one `invalidateQueries()` (post-reconnect reconciliation);
  `onerror` → status `reconnecting` (EventSource retries itself). That plus window-focus refetch
  is the whole recovery story, per the ruling. Judgment call: the connection pill drops the
  third `offline` state — without heartbeat-staleness machinery (excluded by the ruling) nothing
  distinguishes it from `reconnecting`; `App.svelte` and the e2e adapt.
- Routes (9 files + `App.svelte`): `resourceCatalogue.x()` handles → `createQuery`;
  `mutateJsonWithResourceEffect(path, opts, effect)` → a small `mutateJson(path, opts)` helper =
  `fetchJson` then `queryClient.invalidateQueries()` (awaitable where ReviewRoute awaited the
  review refresh). The entire `ResourceMutationEffect` vocabulary dies. Server data must never be
  written into editor/composer component state.
- `window.__plannerDebug` survives in minimal form (`sseOpens`, invalidation flush count) — it is
  the e2e harness's synchronization primitive, not dead machinery.
- Web unit tests: delete the three dead files from `package.json`'s test list; add node-style
  tests for (a) the SSE client (fake EventSource: debounce coalescing, invalidate-on-open,
  status transitions), (b) the query catalogue — every entry's key and path, URL-encoding of
  parameterized ids (coverage the deleted `resource-catalogue.test.mjs` carried), and (c) mutation
  semantics: a failed mutation does not invalidate; a successful one invalidates and is awaitable.

### 8. e2e (rewrites + the acceptance test)

- New `tests/e2e/test_live_update.py`:
  a. change-shows-up-without-refresh: open board, mutate a ticket via the API helper, assert the
     card updates with no reload.
  b. composer acceptance (owner-lived pain, non-negotiable): open a ticket editor, focus, type,
     trigger a server-side change to the same ticket mid-composition, keep typing — assert focus
     retained, full text intact, scroll position unmoved. If this fails against current component
     behavior, fix the component state ownership — that is in scope.
- `test_connection_status.py`: rewrite for SSE (Playwright route-abort `/api/changes` → pill
  `reconnecting`; restore → `connected`; a change made while blocked appears after reconnect).
- `test_resource_catalogue.py`: superseded by `test_live_update.py` — delete.
- `test_trusted_ingress_browser.py` is not a mechanical rename: its wrong-origin rejection proof
  was about the events WebSocket; that proof moves to the conversation WebSocket
  (`/api/conversation`). The SSE endpoint needs no origin gate: it is contentless, and
  cross-origin EventSource reads are CORS-blocked by default (no CORS headers are served).
- Mechanical sweeps in `test_workers_frontend.py`, `test_acp_conversation.py`,
  `test_cli_verbs.py`, `test_server_lifecycle.py`, `test_stage_ownership_frontend.py`,
  `test_flows_a.py`, `test_flows_b.py`, `test_ticket_file_previews.py`, `conftest.py`
  (events-table reads/inserts, retired CLI verb, meta fields, `PLAN_WS_POLL_MS`, debug-gate names).

### 9. docs + AGENTS.md

Update every page describing the old mechanism: `docs/frontend.md`, `docs/README.md`,
`docs/systems.md` + `docs/systems.html`, `docs/tickets-and-gates.md`, `docs/cli.md` (ticket events
retired), `docs/chat.md`, `docs/worker-types.md` (sweep for event-log mentions). AGENTS.md
"Frontend reactivity" note becomes one short paragraph describing: commit-time contentless signal →
SSE ping → invalidate → only mounted queries refetch; TanStack cache; no client store of canonical
state.

## Tickets and sequencing

- **Ticket A — backend** (one Opus agent): §1–§6 + python test updates. Owns `src/planner/`,
  `tests/unit/`, `tests/typing/`, `config.yaml`. Does not touch `web/`, `tests/e2e/`, `docs/`,
  `src/planner/conversation2/`; `conversation/` only the one append_event fallout line.
  Gate: full unit suite + ruff + mypy through the worktree venv.
- **Ticket B — frontend** (one Opus agent, parallel with A — zero file overlap): §7. Owns `web/`
  only. Builds against the fixed SSE contract above (`GET /api/changes`, unnamed `data: change`
  events, comments as heartbeats). Gate: `npm run check`, web unit tests, production build.
- **Ticket C — e2e + docs** (after A+B integrate): §8–§9. Owns `tests/e2e/`, `docs/`, root
  `CLAUDE.md`/`AGENTS.md`, and the `src/planner/skills/` instruction sweep. Gate: targeted
  Playwright run of the new/rewritten e2e files against services started in the worktree.
- Integration is serial; the orchestrator reviews each diff (codex gpt-5.6-sol on the combined
  diff at the end) and runs the focused gates. No `./verify` (program-level rule).

## Explicitly out of scope

`src/planner/conversation/` (beyond the one line), `src/planner/conversation2/`, the conversation
WebSocket and transcript pane, `web/src/vendor/`, per-resource channels, payloads, filtering,
backpressure — nothing speculative.
