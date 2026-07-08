# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log.

## Current work cycle (2026-07-08): chat pending placeholder cleanup

User-reported issue: the ticket chat showed `(none)`/`(non)` above the three thinking dots while a
message was pending. Root cause: `ChatPanel.svelte` adds an empty planner reply slot before the
first streamed token arrives, and that slot rendered through `MarkdownBlock`, whose empty-state
fallback is `(none)`.

Change is intentionally direct and small rather than ticketed: the chat template now renders a
planner message only after it has non-whitespace text, so the pre-token state shows only the
`data-chat-pending` dots. A Playwright regression was added inside the existing chat e2e by
intercepting `/api/chat/*/stream` and holding the stream at `message_start`, then asserting there is
no planner message and no `(none)` text while pending.

Verification status: implementation is staged in the worktree; focused e2e and full `./verify` have
not run yet in this cycle.

## Where we are (2026-07-08): Ticket chat history reload implemented

Current HEAD is `0fc64a8` (`review: upgrade look to match redesigned ticket approval block`), with
the current work cycle adding the ticket chat history reload fix on top. Recent changes:

- `834ef8b` upgraded the Review page (`ReviewRoute.svelte`) to use `ApprovalBlock` with snippet support for actions. The layout was simplified to a plain-text sequence: Ticket Title -> Recap (plain text) -> Note (plain text), followed by a recessed proposed block enclosing the inline `contenteditable` draft, scope picker, and skip/approve actions.
- Ticket chat now reloads the durable Hermes session history when a ticket is opened, revisited, or
  reloaded. The visible rail is the full employee trace: human messages, assistant replies, system or
  command output, and worker-step prompts/replies.
- The history read path uses lazy `session.resume` with `cols` and `source`, so reopening a ticket
  does not build an agent just to display old turns. Hermes remains the source of truth for the
  transcript; the planner DB still persists only the durable `chat_session_key`.
- The Svelte chat panel now hydrates from a `chat:<ticket_id>` resource, forces a fresh reload when
  a cached panel is remounted, refreshes after streamed `message_done`, and maps ticket-domain events
  to `chat:<ticket_id>` so worker updates refetch the trace.
- Tests cover the backend history endpoint, fake-gateway ordered transcript replay, rotated key
  persistence, offline errors, shared-gateway history normalization, event mapping, and the e2e
  send -> board -> return -> reload flow.
- A multi-agent review was attempted but the account usage limit blocked it. A read-only Codex CLI
  review then found one actionable issue: the first draft used non-lazy resume for history reads.
  That finding was accepted and fixed by adding `lazy: true` and asserting it in
  `test_shared_gateway_history_resumes_and_preserves_full_trace`.
- `panels serve` on the default DB exposed an older on-disk schema with `tickets.status` but no
  `tickets.ticket_status`. `create_schema` now performs an idempotent compatibility migration:
  add `ticket_status`, copy old `status` values across, translate `agent_working` to
  `agent_running_step`, and set `PRAGMA user_version=5`. The local default `data/planning.db` was
  migrated successfully; `PLAN_PORT=8791 panels serve` started without the System A missing-column
  exception and was then stopped.
- CLI help was rewritten to be user-facing: command groups now describe what they do, option help
  names accepted values and body-file/stdin behavior, internal spec shorthand was removed, and
  `panels serve` no longer exposes a meaningless `--json` option.
- The live "Gateway Offline" report on ticket chat was not the web server being down. The first
  real gateway error was `Unknown skill(s): panels-worker` because `data/hermes-home/skills` was
  empty. Startup now provisions symlinks for this repo's `skills/panels` and
  `skills/panels-worker` into the configured Hermes home before the shared gateway starts. The next
  real error was missing inference provider config in the dedicated home; the local
  `data/hermes-home/.env` and `data/hermes-home/config.yaml` now symlink to the user's Hermes config
  files. Those links are under gitignored `data/`.
- The first chat turn on a ticket exposed an ordering bug: the employee could call
  `panels worker my-ticket` before the stream persisted the newly created Hermes session key onto
  the ticket. Streaming gateways now emit an internal session-key chunk immediately after
  create/resume; `chat.service.stream` persists it before the prompt runs, and filters that internal
  chunk out of SSE.

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

Ticket chat history implementation details:

- `GET /api/chat/{entity_id}/history` is human-only, resolves the chat entity, returns an empty
  transcript when no session key exists, reads Hermes history through the gateway adapter when a key
  exists, and persists a rotated key if Hermes reports a newer durable tip.
- `SharedGateway.history` uses lazy `session.resume` instead of normal resume. It normalizes roles
  and nested text without filtering worker/system/tool messages, because owner intent is the full
  trace.
- `ChatPanel.svelte` treats Hermes history as the source on mount/reopen and keeps local transcript
  state only for an active in-flight streamed turn.
- `docs/chat.md`, `docs/frontend.md`, and `decisions.md` now describe this behavior.

Verification in this cycle:

- Before the lazy-history review fix, focused chat/minds/frontend tests and full `./verify` passed.
- After the lazy-history fix, `.venv/bin/pytest
  tests/unit/test_minds.py::test_shared_gateway_history_resumes_and_preserves_full_trace
  tests/unit/test_chat_seed.py -q`, `npm --prefix web run test`, and `git diff --check` passed.
- Fresh `./verify` passed after the lazy-history fix: ruff, mypy, 129 unit tests, compile/static
  checks, Svelte check/build/test, and 19 e2e tests all passed. The only warnings were the known
  two Python test warnings and the known three `TicketRoute.svelte` initial-`id` capture warnings.
- After the schema migration fix, focused DB/System A tests passed and fresh `./verify` passed:
  ruff, mypy, 130 unit tests, compile/static checks, Svelte check/build/test, and 19 e2e tests all
  passed. The only warnings were the known two Python test warnings and the known three
  `TicketRoute.svelte` initial-`id` capture warnings.
- After the CLI help pass, `panels --help` and representative subcommand help were inspected, and
  `.venv/bin/python -m compileall -q src/planner/cli` plus
  `.venv/bin/pytest tests/e2e/test_cli_verbs.py -q` passed. Run fresh `./verify` again before the
  final claim.
- After the live gateway fixes, focused chat/minds tests passed for early stream-key persistence and
  skill provisioning. The restarted local server reports gateway status healthy, and
  `HERMES_SESSION_KEY=20260708_154053_d4085e panels worker my-ticket --json` resolves
  `t_0jb9s3sh`. Run fresh `./verify` again before the final claim.

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
actually follows the skill, finds `panels` on `PATH`, and files the proposal. `panels` is now
available globally through `/Users/khushaljagota/.local/bin/panels`, a symlink to this repo's
`.venv/bin/panels`.

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
- The untracked `orchestration/dogfood-report.md` is a prior test-mode/echo-gateway dogfood report,
  not evidence of the real Hermes worker loop.
