# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log.

## Where we are (2026-07-08): Review screen redesigned; Svelte E2E and unit tests passing

Current HEAD is `834ef8b` (`review: upgrade look to match redesigned ticket approval block`). Recent changes:

- `834ef8b` upgraded the Review page (`ReviewRoute.svelte`) to use `ApprovalBlock` with snippet support for actions. The layout was simplified to a plain-text sequence: Ticket Title -> Recap (plain text) -> Note (plain text), followed by a recessed proposed block enclosing the inline `contenteditable` draft, scope picker, and skip/approve actions.
- Svelte check, build, unit tests, and Playwright e2e tests all pass under a clean verification run: **VERIFY: PASS**.

The stale pre-rename server process on port 8767 was killed on the owner's instruction. The default
`data/planning.db` is from an older runtime attempt and should not be reused for the live smoke
because its schema/status history predates the current `ticket_status` code. Use a fresh isolated
DB path.

## What was just learned

The Claude transcript ended immediately after finding the DB-path knob:

- It found `PLAN_DB_PATH` / `PLAN_LOGS_DIR`.
- It had already committed the `panels` rename.
- It did not start the 8799 isolated live smoke before hitting the weekly limit.

The server that first showed echo replies on port 8799 was intentionally fake: it was started with
`PLAN_TEST_MODE=1`, and `gateway_adapter=auto` resolves to the echo fake in test mode. A live
non-test server on the same port, using `PLAN_DB_PATH=data/dogfood-live-codex.db`,
`PLAN_HERMES_HOME=~/.hermes`, and `.venv/bin` on `PATH`, proved ticket chat is real Hermes:

- `/api/meta` returned `test_mode:false`.
- Direct `/api/chat/t_xvemccy4/stream` returned streamed tokens for `live hermes ok` with a real
  session key, `20260708_032210_fb0a07`.
- The Svelte ticket chat in the in-app browser returned `browser live ok`.

That live server was stopped at the end of that dogfood turn; no live dogfood server is intentionally
left running by the current cycle.

The owner's current frontend ruling: treat the Svelte app in `web/` as canonical for UI direction.
FastAPI `/` now serves `web/dist/index.html`; `/_app` remains only as the Vite chunk mount because
the build uses `base: "/_app/"`. The Playwright e2e harness opens `/`, so the browser suite now
asserts the Svelte app directly.

The old classic JS route loop has been removed: `assets/api.js`, `assets/app.js`,
`assets/components.js`, `assets/config.js`, and `assets/screens-*.js` are deleted. Keep
`assets/tokens.css`, `assets/app.css`, and `assets/markdown.js`; the Svelte document still imports
them as shared styling/markdown infrastructure.

The Svelte Review stale-card bug was route-level, not backend/event-mapping:

- Backend accept was correct: `/api/queues` returned `{"approvals":[],"overdue":[]}` and the ticket
  moved to `needs_approach`, `at_cap=stop`.
- The queue resource did refetch empty data, but `ReviewRoute.svelte` fell back from the skipped
  stale entry to `entries[0]`, and another effect reset skip state when every entry was skipped.
- The fix removes the fallback to skipped stale entries, triggers a guarded queue refresh when
  ticket detail proves an entry stale, and renders stale detail/entry mismatches as loading instead
  of a fake empty proposal card.
- Browser verification against live ticket `t_cmhh5hb5`: accepting the Svelte Review proposal
  removed the card, cleared the Review badge, and left `/api/queues` empty.

Ticket chat history investigation (owner concern: leaving and returning to a ticket loses the chat):

- Current behavior is frontend-local only: `ChatPanel.svelte` owns an in-component `transcript = []`,
  appends streamed turns there, and never fetches prior turns on mount. Route keying remounts the
  ticket screen, so the visible chat disappears even though the Hermes session continues.
- The planner DB currently persists only `chat_session_key` plus `chat_session_created`; it does not
  persist chat turns. `src/planner/chat/service.py` sends/streams through the gateway and only
  writes a key when the gateway mints, remints, or rotates it.
- Local Hermes spike notes are enough; do not read `~/.hermes/hermes-agent/` for this. They prove
  `session.resume` returns `message_count` and `messages`, and the gateway exposes
  `session.history`, so a history route can recover existing durable Hermes transcripts.
- Owner ruling: the ticket chat rail is the **full employee trace**, not just human-origin chat.
  Worker step prompts and worker replies are useful and should be visible when returning to a
  ticket.
- Recommended path: add `GET /api/chat/{entity_id}/history` backed primarily by Hermes durable
  session history (`session.resume` messages or `session.history`), hydrate `ChatPanel` from a
  Svelte `chat:<entity_id>` resource, and keep live streaming append behavior for the active turn.
  A planner projection is optional only as a cache/index if Hermes history proves too slow or too
  awkward to normalize; it should not filter worker prompts out. E2E must assert send → navigate
  away → return and reload both preserve the visible turns.

Targeted verification passed in this cycle:

- `npm --prefix web run check` — 0 errors, 3 Svelte warnings, all in `TicketRoute.svelte` capturing
  the `id` prop at mount.
- `npm --prefix web run test` — event-mapping test passed.
- `npm --prefix web run build` — build passed; Vite warns that `/assets/tokens.css`,
  `/assets/app.css`, and `/assets/markdown.js` are runtime-served, and repeats the 3 TicketRoute
  warnings.
- `.venv/bin/pytest tests/e2e -q` — 19 e2e tests passed against Svelte at `/`.
- `./verify` — ruff, mypy, 123 unit tests, compile/static checks, Svelte check/build/test, and
  19 e2e tests all passed.

## Current hypothesis

The worker loop wiring is probably sufficient now:

- `SharedGateway` starts one child with `HERMES_HOME=$PLAN_HERMES_HOME` and
  `HERMES_TUI_SKILLS=panels-worker`.
- `System A` polls today's empty runnable tickets.
- `System B` sets `ticket_status=agent_running_step`, sends the next-step prompt through the shared
  gateway, persists a rotated `chat_session_key`, and clears or parks status when the proposal
  lands.
- The symlinked `~/.hermes/skills/panels` and `~/.hermes/skills/panels-worker` point at this repo's
  skill files.

The most likely remaining failure is not Python wiring but live-agent behavior: whether the worker
actually follows the skill, finds `panels` on `PATH`, and files the proposal.

## Immediate next step

Run the isolated live worker smoke:

1. Start `panels serve` on port 8799 with a fresh DB/log/lock path, `PLAN_HERMES_HOME=~/.hermes`,
   and `PATH="$PWD/.venv/bin:$PATH"`.
2. Create a fresh ticket and add it to today.
3. Set scope so the first step parks a `success` proposal rather than auto-advancing past the proof.
4. Wait for System A/B to run.
5. Inspect the ticket, status events, and server/gateway logs.
6. If it passes, update this file with the full smoke result and then run fresh `./verify` before
   claiming progress.

## Known gaps

- The next-step prompt still names the ticket id/title directly, so `panels worker my-ticket` is
  wired and unit-tested but not forced by the live prompt yet.
- Errored recovery is still missing; errored tickets cannot be retried or cleared.
- Rollover / daily boundary agent work is not rebuilt yet.
- Svelte warnings remain in `TicketRoute.svelte`: ticket resources capture `id` at mount. This is
  safe while `App.svelte` keeps `{#key route.key}` around the route, so ticket-id navigation remounts
  the component; clean it up if route keying is ever removed or TicketRoute becomes reusable in-place.
- Ticket chat history is not fetched back into the ticket page yet. Owner ruling: the page should
  reload the full Hermes employee trace, including worker prompts and replies.
- The untracked `orchestration/dogfood-report.md` is a prior test-mode/echo-gateway dogfood report,
  not evidence of the real Hermes worker loop.
