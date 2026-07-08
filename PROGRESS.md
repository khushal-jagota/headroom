# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log.

## Current work cycle (2026-07-08): Board Workspace-left correction

Owner correction: the Board must copy the old planning-server Workspace left rail, not a generic
top-down list. The right inspector can stay minimal for now; the current priority is getting the
left chooser shape right.

Current approach:

- Keep the backend board contract unchanged (`GET /api/board` columns in state order).
- Make the Board own the full height below the app nav, like the old Workspace shell.
- Render the left rail as the old Workspace chooser shape: `Refinement Tree` heading, uppercase
  state sections, chevrons, counts, and transparent row buttons.
- Render every state header in order; do not filter empty states.
- Keep rows flat in the rail; no generic EntityRow/card treatment.
- Preserve existing e2e selectors: `data-column`, `data-card`, and `data-ticket-id`.

Result:

- `web/src/routes/BoardRoute.svelte` now renders the Board as a Workspace split with a left
  Refinement Tree chooser. The right side is only a minimal selected-ticket link for now.
- `assets/app.css` replaces the generic Board list/card styling with the old Workspace-left shape:
  full-height split shell, scrollable left rail, uppercase mono section headers, counts, chevrons,
  transparent rows, and hover/active row fill only.
- `docs/frontend.md` describes Board as a Workspace-style left rail.
- The row title keeps the legacy `.entity-row-title` compatibility hook so the existing reload e2e
  can keep asserting Board state without restoring the old EntityRow visual treatment.

Verification status: `./verify` passed on 2026-07-08. Gates passed: ruff, mypy over 80 source files,
159 unit tests, compile/static checks, `npm --prefix web run check`, `npm --prefix web run build`,
`npm --prefix web test`, and 19 e2e tests. The known Svelte `TicketRoute.svelte` initial-value
warnings and Pytest `TestClock` collection warnings remain.

### Side planning note: CLI redesign plan

Owner request: plan the move to the simpler CLI shape and use built-in subagents as
reviewers.

- Added `orchestration/cli-redesign/plan.md`.
- The planned command shape is `panels day ...`, `panels ticket ...`,
  `panels sprint ...`, and `panels worker ...`.
- Worker commands are separate from ticket management. `worker propose` infers the
  current proposal field from ticket state and must include a recap update; `worker
  recap` remains available for recap-only updates.
- Ticket creation stays under `ticket create`; sprint commands may place existing
  tickets into a sprint or sprint item, but do not create tickets.
- Day listing is explicit: `day show` includes overview plus tickets, while
  `day list-tickets` is the focused scanner/script command.
- Code-owned state and runtime status are deliberately out of CLI scope.
- Two built-in reviewer subagents reviewed the plan. Accepted fixes: remove old
  top-level command homes instead of aliasing them, remove visible `idea` from this
  CLI shape, remove post-create `ticket set sprint`, make `day set` field-first
  with `--date`, keep broad sprint-item status knobs out of the first wave, add the
  missing backend writer/API for moving existing tickets under sprint items, allow
  proposal-with-recap to write recap on the first `needs_success` proposal, spell out
  blocker link direction, and add `current`/`none` selector tests.

Verification status: planning-only; no product code changed and no `./verify` run.

## Prior work cycle (2026-07-08): system-friction cleanup implementation

Owner request: implement and review the first four system-friction cleanup
tickets from `orchestration/system-friction-cleanup/plan.md`.

Result:

- SF1 moved obvious API-local writers into canonical sprint/ticket data writer
  modules: idea creation, sprint date edits, ticket title edits, and ticket
  project edits.
- SF2 removed retired claim/run/breaker runtime knobs from live config while
  preserving compatibility for stale local yaml/env keys by ignoring them.
- SF3 made `dispatch_enabled` explicitly a System A startup switch in code,
  comments, config, and tests.
- SF4 moved shared Hermes run contracts to `planner.minds.contracts`, kept
  production on `SharedGateway`, and left `run_step` as a smoke/helper primitive
  instead of a package-level export.
- Live systems docs and the HTML artifact now show only the remaining system
  frictions: chat session-key ownership, the two blocking shapes, frontend
  state-machine copies, and indirect gateway bootstrap.
- The two blocking shapes stay out of scope for the later owner discussion.

Review status:

- Codex CLI read-only review of SF1 reported `NO VIOLATIONS`.
- Codex CLI read-only review of SF4 reported `NO VIOLATIONS`.

Verification status: first post-implementation `./verify` passed on 2026-07-08:
ruff, mypy over 80 source files, 159 unit tests, compile/static checks,
frontend check/build/test, and 19 e2e tests. Known warnings remain the existing
Svelte initial-value warnings in `TicketRoute.svelte` and Pytest's `TestClock`
collection warnings.

## Prior work cycle (2026-07-08): today tickets stopped at needs-success

Owner request: correct today's imported tickets so they all sit on today's day at
`needs_success` with `at_cap=stop`, and stop the accidental worker activity for
now.

Actions:

- Terminated the live default `panels serve` process tree that had spawned six
  ticket workers from the imported day.
- Backed up the active DB to
  `data/migration-backups/planning.db.before-today-stop.20260708-202520.bak`.
- Repaired every ticket on `day_2026-07-08` in one SQLite transaction:
  `state=needs_success`, `ceiling=needs_success`, `at_cap=stop`,
  `ticket_status=empty`, `chat_session_key=NULL`, and all parked field
  proposals cleared.
- Preserved today's six ticket placements and their order.
- A new `panels serve` process came back up after the repair, but no matching
  worker children are running. With today's tickets at ceiling plus stop, System
  A's readiness predicate returns false for them.

Verification status: direct DB checks passed: 6 tickets remain on
`day_2026-07-08`; 0 today tickets violate the requested
`needs_success`/`needs_success`/`stop`/`empty`/no-proposal shape. No `./verify`
run because this was a live DB data repair, not a code change.

## Prior work cycle (2026-07-08): top-down Board redesign

Owner request: change the Board from a kanban-style horizontal column layout into a more top-down
execution view like the old planning server.

Current finding:

- The backend board contract is already right: `GET /api/board` returns today's day-scoped tickets,
  grouped by ticket state, with priority/deadline/project/proposal/runtime markers.
- This is a UI-shape change only. The Board should stay the same day-scoped System A surface and
  keep the existing `data-column` / `data-card` e2e hooks.
- The implementation can stay narrow: `web/src/routes/BoardRoute.svelte`, `assets/app.css`,
  `docs/frontend.md`, plus memory bookkeeping. No new endpoint, no client-side canonical store.

Result:

- `web/src/routes/BoardRoute.svelte` now renders the Board as a single top-down stack of non-empty
  ticket stages instead of horizontal kanban lanes.
- `assets/app.css` replaces the kanban card-column treatment with a centered vertical execution
  list and responsive single-column behavior.
- `docs/frontend.md` now describes the Board as a top-down stage stack.
- The existing e2e hooks are preserved: stage sections still expose `data-column`, and ticket rows
  still expose `data-card` / `data-ticket-id`.
- While running `./verify`, an unrelated dirty-worktree mypy failure in `src/planner/sprints/data.py`
  surfaced (`create_idea` returning an untyped SQLite row). Added a narrow `cast` so the existing
  return contract is explicit; no behavior changed.

Verification status: `./verify` passed on 2026-07-08. Gates passed: ruff, mypy, 154 unit tests,
compile/static checks, `npm --prefix web run check`, `npm --prefix web run build`,
`npm --prefix web test`, and 19 e2e tests. The remaining frontend diagnostics are the pre-existing
three Svelte initial-value warnings in `TicketRoute.svelte`.

## Prior work cycle (2026-07-08): microphone support plan proposal

Owner request: work ticket `t_ykfra3vz` and propose its `plan` field.

Current finding:

- The ticket is `needs_plan` and asks to publish already-built Vylo microphone support.
- Its settled success condition is: prod has the xAI key set, local `main` is pushed, and the chat
  composer can transcribe through the normal chat path in the deployed environment.
- The imported V1 context says voice input was built and device-tested locally, the provider decision
  is xAI Grok batch STT, and the boundary is publication only: do not reopen provider or design
  exploration unless prod testing exposes a real fault.
- This session is running inside `/Users/khushaljagota/.hermes/planning-v2`; the actual Vylo repo is
  outside this workspace boundary. The plan therefore starts by handing execution to a worker in the
  real Vylo repo or to an owner-approved repo override.

Result: drafted a lean publish plan, but `panels propose plan t_ykfra3vz --body-file -` was rejected
by the server with `at_cap_stop: ticket is at its ceiling with at_cap=stop`. No plan proposal was
parked on the ticket; it needs a human scope change or correction before an agent proposal can land.
No product code changed.

Verification status: no `./verify`; this step only proposes a ticket plan and updates markdown
bookkeeping.

## Prior work cycle (2026-07-08): worker poll/chat propagation approach

Owner request: work ticket `t_dpentg38` and propose its `approach` field.

Current finding:

- The ticket is `needs_approach`; its success notes ask for an investigation into why worker polling
  and worker chat history did not appear to propagate after a settled success edit.
- Current code/docs already encode the expected shape: System A polls only today's `empty` tickets,
  readiness is checked again by System B, worker runs use the ticket's durable chat session, and the
  chat panel retries worker history while/after a run so early empty history does not stick.

Next step: propose an approach that starts with live reproduction/classification, traces the System
A → System B → chat history route, separates product-rule questions from defects, and requires
focused tests plus `./verify` for any fix.

Verification status: no product code changes in this approach-only step.

## Prior work cycle (2026-07-08): landing-page Gate 1 worker attempt

Owner request: work ticket `t_gehbw18n` and propose its `result` field.

Current finding:

- The ticket is `in_progress` and asks for the Vylo landing page Gate 1 to be shippable enough to
  push `main`. Its settled success condition is that a cold visitor roughly understands Vylo,
  sees craft, is not bored, and is curious; the ticket notes name Vylo, dirty external landing
  work, `.claude/plans/landing-page-gate1.md`, `LandingPage.tsx`, reveal-list and brandmark
  assets, metadata, and a Gate-1-only boundary.
- This session is running inside `/Users/khushaljagota/.hermes/planning-v2`, whose `AGENTS.md`
  explicitly scopes work to this repository and says not to read from or write to any other repo.
- Narrow repo inspection found no `LandingPage.tsx`, no `.tsx` files, and no
  `.claude/plans/landing-page-gate1.md`; the only landing-page context here is the imported v1
  planning snapshot.

Result: no Vylo landing-page code was changed or pushed from this worker. I drafted the result field
as a boundary report saying the work could not be completed from this workspace and needs a worker run
in the actual Vylo repo, or an explicit owner override naming that repo/branch. The `panels propose
result t_gehbw18n` call was rejected by the product gate with `at_cap_stop` because the ticket's
scope is `ceiling=in_progress, at_cap=stop`; the API will not park a `result` proposal until the human
raises the scope past `in_progress`.

Verification status: no product code changes and no `./verify`; `git diff --check` is enough for the
markdown-only bookkeeping edits in this worker attempt.

## Prior work cycle (2026-07-08): current sprint V1 cutover

Owner request: migrate the current sprint from planning V1 markdown into this v2
system, treating V1's current sprint and daily ticket files as the source of
truth. This is a scoped owner override to read
`/Users/khushaljagota/.hermes/planning` for the current sprint only; no V1 files
were written.

Current approach:

- Added `scripts/migrate_current_sprint_from_v1.py` as a one-off current-sprint
  cutover script. It imports only `sprints/current/sprint-kickoff.md`,
  `sprint-review.md`, `sprint-tracking.md`, and every
  `sprints/current/daily/YYYY-MM-DD/{overview,tracker,workspace}.md`.
- Excluded V1 `deferred.md` and `ideas.md` because the request was to migrate
  just the current sprint and the referenced daily tickets.
- Replaced the active v2 planner records with the imported current sprint. The
  exact pre-cutover DB was backed up to
  `data/migration-backups/planning.db.20260708-195040.bak`.
- Preserved existing event rows to keep the event log append-only, then appended
  fresh events for the imported records.
- Active DB now has: 1 sprint, 13 sprint items, 10 unique tickets, 8 days, 35
  day-ticket placements, 0 ideas, 1 link, and 295 total event rows. The event
  table is higher than the first import because two running `panels serve`
  processes picked up today tickets immediately after migration; they were
  stopped with SIGTERM and the migration was reapplied cleanly, preserving the
  event rows rather than deleting them. The post-dispatch intermediate DB was
  backed up to `data/migration-backups/planning.db.20260708-195325.bak`.
- Final imported tickets have `ticket_status=empty` and no parked proposals.
- The latest day, `day_2026-07-08`, now contains six V1 tickets in order:
  publish microphone support, landing page Gate 1, durable-personas exploration,
  app typography pass, planning-v2 worker poll/chat propagation, and planning-v2
  gateway smoke/home provisioning.

Independent review:

- Codex CLI read-only review caught three script issues before active apply:
  backup was after schema creation, daily overview/tracker sections could be
  silently dropped, and default DB path resolution depended on the caller's cwd.
  All three were fixed.
- Follow-up Codex review caught that clearing `events` still violated the
  append-only event model even without resetting ids. Accepted fix: do not clear
  `events`; keep old event rows and append migration events.

Verification status: `./verify` passed on 2026-07-08. Gates passed: ruff, mypy,
149 unit tests, compile/static checks, `npm --prefix web run check`,
`npm --prefix web run build`, `npm --prefix web test`, and 19 e2e tests. The
remaining frontend diagnostics are the pre-existing three Svelte initial-value
warnings in `TicketRoute.svelte`.

## Prior work cycle (2026-07-08): CLI capability/design exploration

Owner request: map what it would take for the planner to be fully operable from
the CLI, especially sprint planning, day planning, and day/ticket operation, and
compare possible CLI designs before implementation.

Current understanding:

- The current `panels` CLI is deliberately agent-shaped. It can create/list/show/set
  tickets, items, ideas, day ticket membership, links, proposals, recaps, notes, and
  queues, but it has no verbs for human-only decisions such as accept, approve, scope,
  state jump, drop, takeover/release, day overview edits, sprint creation/editing, item
  status acceptance, or chat.
- The backend already exposes most missing primitives as HTTP routes. The central gap is
  the CLI authority model and command design, not database capability.
- `docs/cli.md` says the CLI is for workers and developer debugging and intentionally
  cannot approve/grant/unblock. `skills/panels/SKILL.md` says everything runs through
  `panels` and the CLI is how to read and change everything. That mismatch is the first
  product decision.
- Owner clarification: the CLI does not need to perform human decisions. The human action
  is approval. Code/runtime-owned changes such as ticket state, ticket status, and worker
  control should remain code-owned, not exposed as broad manual CLI knobs.
- The likely command surface should keep worker/operator primitives for scripts and add
  workflow aliases for the common jobs: plan a sprint, plan today, operate today, and read
  what needs approval. Approval itself stays outside the CLI unless the owner later makes
  a narrower product call.

Verification status: no code changes and no `./verify`; planning/read-only analysis plus
this memory update only.

## Prior work cycle (2026-07-08): system-friction cleanup plan

Owner request: plan the first four system-friction fixes: canonical writer
ownership, retired runtime config, dispatcher switch semantics, and Hermes run
primitive ownership. The two blocking shapes are intentionally deferred for a
separate owner discussion because they are important product semantics, not
simple cleanup.

Current approach:

- Added `orchestration/system-friction-cleanup/plan.md`.
- The plan defines four serial tickets: SF1 writer relocation, SF2 retired config
  removal, SF3 startup-only dispatcher switch semantics, and SF4 explicit Hermes
  primitive ownership.
- It keeps chat session-key writers out of SF1, keeps blocking-shape unification
  out of scope, and requires Codex reviews for SF1 and SF4.
- Codex CLI reviewed the plan and found three concrete issues. All were accepted
  and folded into the plan: SF4 now preserves production-used `RunResult` /
  `OnEvent` typing, SF1 now requires focused public route tests for successful
  title/project patches, and SF1 pins the current parented-project error code
  and message.

Verification status: `./verify` passed on 2026-07-08 after the plan-review
corrections. Gates passed: ruff, mypy, unit suite, compile/build checks,
frontend checks/build/test, and e2e suite.

## Prior work cycle (2026-07-08): systems HTML artifact

Owner request: turn the systems document into a designed HTML artifact, open it,
and keep the same minimalism standard in the design: no over-carding, light
background treatment instead of borders, and progressive disclosure.

Current approach:

- Added `docs/systems.html` as a static companion to `docs/systems.md`.
- Kept the artifact typographic and operational: a single system-spine visual,
  short question-based orientation, collapsible system rows, concise boundaries,
  and collapsible friction details.
- Linked the artifact from `docs/README.md`.
- Opened the final artifact and visually checked desktop and mobile screenshots.

Verification status: `./verify` passed on 2026-07-08. Gates passed: ruff, mypy,
unit suite, compile/build checks, frontend checks/build/test, and e2e suite.

## Prior work cycle (2026-07-08): system-design map and docs

Owner request: read the repo from a system-design perspective, label the systems,
identify structures that are less logical or less cleanly segmented, and write a
minimal human-readable systems document for someone cold to the codebase.

Current understanding:

- The planner's real source of truth is SQLite. Canonical writer functions mutate
  records and append events; the event log is a doorbell for targeted frontend
  refetch, not a second data model.
- The main product systems are: the record system, planning objects, ticket gate,
  employee runtime, Hermes gateway, chat, frontend resources, and CLI/authority.
- Tickets carry two separate states: `state` for the work gate and `ticket_status`
  for runtime control. The resolution engine owns field values and `state`; runtime
  writer functions own `ticket_status`.
- System A polls only today's day tickets. System B runs one Hermes turn on a ticket's
  durable session. Ticket chat and worker steps use that same session; chat sends are
  rejected while a worker step is active.
- The frontend is event-driven but not event-sourced: events map to resource keys
  and trigger refetches from the backend.

System-level friction found:

- `src/planner/sprints/api.py` still contains private "gap-fill" writers for ideas
  and sprint-date edits. `src/planner/tickets/api.py` also contains private title and
  project writers, and `src/planner/chat/service.py` owns chat session-key writes.
  These are working exceptions to the otherwise clean writer-module boundary.
- `config.yaml` and `core.config.Config` still expose unused retired runtime knobs:
  `claim_ttl_seconds`, `max_runs`, `failure_limit`, and `run_max_seconds`.
- Both `SharedGateway` and `minds.runner.run_step` exist as Hermes run primitives,
  but production uses `SharedGateway`.
- Blocking has two shapes: runtime `links.kind='blocks'` and sprint-item
  `blocked_by` JSON.
- `web/src/lib/ui.ts` repeats ticket state-machine constants for rendering.
- The real gateway adapter is a registry placeholder that production startup replaces
  with the app-state `SharedGateway` owner, which lazily spawns the gateway child on
  first use. This is correct but non-obvious.
- `dispatch_enabled` is checked when System A starts; the helper/comment trail implies
  per-tick re-reading, but no live runtime caller currently does that.

Changes in this cycle:

- Added `docs/systems.md` as the cold-start system map and boundary/friction list.
- Linked the new doc from `docs/README.md`.
- Corrected `docs/backlog-and-ideas.md` so it no longer says day chat captures loose
  work; current Day and Chat docs say day chat is not a current UI surface.

Independent review: Codex CLI read-only review accepted three doc fixes: narrow the
writer-boundary claim, clarify that `SharedGateway` spawns the child lazily, and say
the Board shares today's membership with System A rather than the exact runnable set.

Verification status: `./verify` passed on 2026-07-08. Gates passed: ruff, mypy,
unit suite, compile/build checks, frontend checks/build/test, and e2e suite.

## Prior work cycle (2026-07-08): worker poll/chat propagation investigation

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
