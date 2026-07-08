# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log.

## Current work cycle (2026-07-08): worker poll/chat propagation investigation

Owner concern: the worker poll does not appear to drive the intended UX. Changing a settled success
condition appears to do nothing, and the worker wake message that should be visible in ticket chat
history is not visible.

The first pass read `docs/employee-runtime.md`, `docs/chat.md`,
`src/planner/runtime/system_a.py`, `src/planner/runtime/system_b.py`,
`src/planner/runtime/readiness.py`, `src/planner/tickets/api.py`,
`src/planner/chat/service.py`, `src/planner/minds/shared_gateway.py`,
`web/src/components/ChatPanel.svelte`, and the runtime/chat tests.

Current intended-behavior model:

- System A polls only tickets on today's day and only those whose durable `ticket_status` is `empty`.
- Readiness additionally requires a real next gating field, no parked proposal on that gating field,
  scope that permits the next proposal, and no open blocker.
- Readiness-changing human actions should poke System A immediately; the timer is only a backstop.
- System B re-checks readiness at execution time, writes `agent_running_step`, sends exactly one
  next-step prompt into the ticket's durable Hermes session, then clears to `empty` or parks at
  `awaiting_approval` if the worker filed a proposal.
- The same durable Hermes session is the ticket chat source, so worker-step prompts/replies should
  appear in `/api/chat/{ticket_id}/history` and therefore in the ticket chat rail after event-driven
  invalidation/refetch.

Latest live-root-cause pass:

- The owner's live ticket from the logs, `t_0jb9s3sh`, was not on today's day. It was on
  `day_2026-07-07`; the actual today resource was `day_2026-07-08` and had no tickets. Direct DB
  inspection showed `readiness.is_runnable(conn, t_0jb9s3sh) == True`, but System A's candidate SQL
  returned `[]` because it joins `day_tickets` on today's id. Scope edits were writing
  `scope_changed` events and poking, but the ticket was outside the auto-run set.
- A real harmless dogfood ticket on the actual DB, `t_a89826gz`, proved the worker path works when a
  ticket is on today and receives a wake: scope poke moved it `empty -> agent_running_step ->
  awaiting_approval`, filed a success proposal, and `/api/chat/t_a89826gz/history` later returned
  the System B prompt plus worker reply.
- That same live run exposed a frontend timing bug: the immediate history read at the settled
  `awaiting_approval` event returned zero messages, while a later history read returned the worker
  prompt/reply. A mounted chat panel could therefore cache the early empty read and never receive a
  later invalidation.

Changes made in this cycle:

- `PUT /api/tickets/{id}/value/{field}` now takes `SystemA` and pokes it after a successful settled
  field edit. This makes success-condition edits wake the worker immediately instead of waiting for
  the timer.
- System B now persists a newly created or resumed `chat_session_key` as soon as the gateway has it,
  before `prompt.submit`. That closes the identity race where a live worker could call
  `panels worker my-ticket` during the turn and briefly get 404 for its own fresh session key.
- A read-only Codex review found a second first-session race: a human chat could create a separate
  ticket session while System B was claiming the worker's first key. Accepted fix: active worker
  steps own the ticket session, so chat sends and commands now return `already_running` while
  `ticket_status=agent_running_step`; history remains readable.
- A follow-up Codex review found two System B ownership holes. Accepted fixes: the worker callback
  now re-checks ownership even when resuming the same stored session key, and System B error
  settlement uses a guarded writer that only marks `errored` if the ticket is still
  `agent_running_step`. A second read-only Codex review reported no violations.
- The ticket page header now always shows the durable `ticket_status`, so `empty`,
  `agent_running_step`, `awaiting_approval`, `user_takeover`, and `errored` are visible in the UI.
- The ticket page header now also shows an `auto` chip: `not on today`, `eligible`, `running`,
  `awaiting approval`, `blocked`, `human review`, or `stopped at limit`. This makes the today-scoped
  System A candidate rule visible instead of leaving `empty` to carry too much meaning.
- `/api/board` now uses the same today scope as System A: it only returns tickets on today's day.
  The Board is now the execution board, not an all-ticket inventory.
- `POST /api/day/{date}/tickets` now pokes System A after a successful add. Adding a ready ticket to
  today's list is a readiness-changing action and should not wait for the next poll tick.
- `ChatPanel.svelte` now keeps refreshing history while `ticket_status=agent_running_step` and runs
  a small bounded retry after `awaiting_approval` or `errored` only while the transcript is still
  empty, so an early empty Hermes history read does not permanently hide the worker prompt/reply.
- The owner clarified that `planner serve` was a misstatement. `panels serve` remains the only
  console startup command; packaging now has a regression test that `panels` is installed and
  `planner` is not. A non-test isolated startup smoke verified that the serve path serves
  `/api/meta` and `/`, provisions the `panels` and `panels-worker` role skills into
  `PLAN_HERMES_HOME`, and creates the dispatcher lock for System A. The latest smoke used
  `.venv/bin/panels serve` directly after reinstalling the package and confirming `.venv/bin/planner`
  is absent.
- A user-run `panels serve` exposed a cwd bug: the global script failed when launched from a directory
  without `assets/`. Fix: server static mounts (`web/dist`, `assets`, `static`) now resolve from the
  repository root, and `serve` loads repo-root `config.yaml` after changing into the repo root. A
  smoke launched `/Users/khushaljagota/.local/bin/panels serve` from a directory with no `assets/`
  and verified `/`, `/assets/app.css`, `/static/favicon.ico`, skill provisioning, and the dispatcher
  lock.
- Tests now cover value-edit wakeups, early session-key lookup, System B prompt/reply visibility
  through ticket chat history, chat rejection while a worker step is active, worker ownership loss
  before prompt submit, guarded error settlement, the `panels` console-script contract,
  and the ticket status chip in the existing chat e2e.

Live non-test smokes:

- Fresh server on port 8892 with an isolated DB: a today ticket advanced from `empty` to
  `awaiting_approval`; the event log included `agent_running_step`, `chat_session_created`,
  `proposal_filed`, and the parked status; `/api/chat/{ticket}/history` returned the System B prompt
  and worker reply.
- Long-timer server on port 8893 with `PLAN_TICK_SECONDS=120`: adding a ticket to today directly did
  not fire within five seconds, then editing its settled success value through the API poked System A
  immediately and produced an approach proposal. The worker's by-session identity lookup returned
  200 during the active turn.
- Actual default DB/server on port 8767 after restarting from the changed code: adding harmless ticket
  `t_88j0b6jp` to `/api/day/today/tickets` moved it to `agent_running_step` on the immediate read
  without any scope poke, then it parked at `awaiting_approval` with a success proposal. Immediate
  history was empty, then `/api/chat/t_88j0b6jp/history` returned two messages after two seconds.
  A Playwright assertion against the actual server confirmed the ticket page showed `status awaiting
  approval`, `auto awaiting approval`, and the worker chat reply.
- Actual default DB/server after the Board scope change: created harmless ticket `t_sjgq8tx9`,
  confirmed `/api/board` did not include it before a day assignment, then posted it to
  `/api/day/today/tickets` and confirmed `/api/board` included it.

Final verification: `./verify` passed on 2026-07-08. It ran ruff, mypy, 149 unit tests,
compile/static checks, `npm --prefix web run check`, `npm --prefix web run build`,
`npm --prefix web test`, and 19 e2e tests. The remaining frontend diagnostics are the pre-existing
three Svelte initial-value warnings in `TicketRoute.svelte`.

## Prior work cycle (2026-07-08): verify command review

Owner concern: full `./verify` is too heavy for the way it is being used after every small change.
No code change has been made in this cycle. The investigation is reading the current verify script,
the e2e harness, and the latest stored JUnit timings rather than re-running the full gate.

Current finding: `./verify` is still the right completeness claim, but it is the wrong inner-loop
command. The script is a single serial gate: skip-scan, ruff, mypy, all unit tests, Python/assets
build check, Svelte check/build/test, then all e2e tests. Last stored timings show unit tests at
about 2.9s and e2e at about 23s; the e2e harness starts a fresh `panels serve` subprocess and temp
SQLite DB for each test, which is good isolation but a real fixed cost.

Current hypothesis: keep `./verify` exhaustive and final-only, then add a first-class fast
verification path for development and ticket implementation. The fast path should run the skip-scan,
format/static checks, relevant focused tests, and only the frontend/e2e slice touched by the diff.
Full `./verify` should run after integration or before claiming done, not after every edit.

Immediate next step: agree on the policy and then implement the smallest command/docs change that
makes the intended workflow obvious.

## Prior work cycle (2026-07-08): chat pending placeholder cleanup

User-reported issue: the ticket chat showed `(none)`/`(non)` above the three thinking dots while a
message was pending. Root cause: `ChatPanel.svelte` adds an empty planner reply slot before the
first streamed token arrives, and that slot rendered through `MarkdownBlock`, whose empty-state
fallback is `(none)`.

Change is intentionally direct and small rather than ticketed: the chat template now renders a
planner message only after it has non-whitespace text, so the pre-token state shows only the
`data-chat-pending` dots. A Playwright regression was added inside the existing chat e2e by
intercepting `/api/chat/*/stream` and holding the stream at `message_start`, then asserting there is
no planner message and no `(none)` text while pending.

Verification status: focused
`.venv/bin/pytest tests/e2e/test_flows_a.py::test_e26_chat_panel_echo_and_offline -q` passed.
Fresh `./verify` passed after the fix: ruff, mypy, 132 unit tests, compile/static checks,
Svelte check/build/test, and 19 e2e tests all passed. The remaining Svelte warnings are the known
three `TicketRoute.svelte` initial-`id` capture warnings.

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
