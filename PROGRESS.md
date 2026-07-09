# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log.

## Current work cycle (2026-07-09): Real ticket hard deletion

Current implementation:

- One human-only transaction now permanently deletes a ticket, refuses any running worker control
  or chat turn, repacks day placement, removes links and Panels chat, and refreshes affected
  day/item/link/sprint/queue resources.
- Prior Planner events owned by or referring to the ticket are pruned before fresh cleanup
  doorbells are written; one minimal `ticket_deleted` event remains as the audit.
- `DELETE /api/tickets/{id}`, `panels ticket delete <id> --yes`, and a confirmed destructive action
  on both standalone and embedded Ticket screens all use the same writer. The route wakes System A
  because deleting a blocking ticket can make another ticket runnable.
- The separate stored Hermes session is explicitly outside the Panels-record deletion boundary.

Verification status:

- Focused delete/event tests passed: 4 unit tests; CLI/browser delete coverage passed: 2 e2e tests.
- Codex's plan review found the active-human-turn race and the event-history contract/scope gaps;
  those were fixed or explicitly scoped. Its implementation review found stale cross-entity event
  references; recursive pre-delete pruning and coverage were added, and the follow-up reported
  `RESOLVED`.
- Full `./verify` passed: ruff ok, mypy ok, 185 unit tests passed, build/frontend gates ok,
  38 e2e tests passed, `VERIFY: PASS`.
- The real mistaken ticket `t_e5yagwfw` was deleted through the new CLI action. Direct lookup now
  returns `not_found`; ticket/day/link/chat rows are zero; it is absent from ticket lists and Review;
  exactly one minimal `ticket_deleted` audit remains.

Immediate next step:

- Propose the result on ticket `t_svb8xkpz`.

## Current work cycle (2026-07-09): Deep-module architecture review

Current result:

- Completed a read-only architecture scan using the deep-module vocabulary and deletion test.
- Opened a six-candidate visual report at
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/architecture-review-20260709-220503.html`.
- Ranked direct employee-turn ownership first: Review rejection can claim
  `agent_running_step` and return success without delivering guidance when System A is absent.
- Proved a second correctness failure with temporary SQLite: a compound Ticket PATCH can return
  validation while preserving an earlier field commit.
- The other surviving candidates are readiness-wake ownership, frontend resource identity and
  invalidation, the Sprint item read projection, and gateway composition/lifecycle.

Verification status:

- Three read-only sub-agent walks independently covered Ticket/core, runtime/Chat, and
  frontend/Sprint seams; the lead checked every promoted candidate against source, tests,
  decisions, and the relevant redesign plans.
- The report contains six before/after diagrams, passed an HTML tag-balance check, and was opened
  with the macOS `open` command.
- No implementation changed, so `./verify` was not rerun for this review-only cycle.

Immediate next step:

- The owner selects a candidate; then run the grilling and domain-modeling loop before proposing
  any interface.

## Current work cycle (2026-07-09): Direct Review rejection worker turn

Current implementation:

- Review rejection clears a pending gated proposal without changing ticket stage or settled values.
- The rejection guidance is sent directly to the existing Hermes worker session as the prompt for an
  already-claimed `agent_running_step`; it is not copied into Panels chat or deferred to System A.
- Agent-running tickets are absent from Review. A rejected final result can be revised and returned to
  Review while the ticket remains `needs_review`.
- The obsolete `approval_returned` and rejection state-change writes are no longer emitted.

Verification status:

- Targeted return-for-revision and System B tests pass: 19 tests passed.
- Focused Review browser coverage passed.
- Codex found duplicate-send, stale-approve, missing-session, and stale-queue-time risks; each was
  fixed, covered, and the final read-only review reported no violations.
- Full `./verify` passed: ruff ok, mypy ok, 182 unit tests passed, build/frontend gates ok,
  36 e2e tests passed, `VERIFY: PASS`.

Immediate next step:

- Propose the result on ticket `t_854bbmqg`.

## Current work cycle (2026-07-09): Keep Hide done across navigation

Current implementation:

- The app shell now owns the Workspace Hide done choice, so leaving and returning to Workspace
  remounts the screen with the current choice instead of resetting it.
- The Workspace toggle remains the writer for that choice, and done-ticket filtering still uses the
  same value.
- Workspace browser coverage now checks both enabled and disabled choices after navigating to Day
  and back.
- `docs/frontend.md` describes the navigation behavior.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` warnings.
- `npm --prefix web run build` passed and refreshed the checked-in frontend bundle.
- `.venv/bin/python -m pytest tests/e2e/test_board_stage_indicators.py -q` passed: 2 tests passed.
- Full `./verify` passed: ruff ok, mypy ok, 181 unit tests passed, build/frontend gates ok,
  36 e2e tests passed, `VERIFY: PASS`.

Immediate next step:

- Propose the result on ticket `t_mu16dnhj`.

## Current work cycle (2026-07-09): Worker chat context boundary

Current implementation:

- `AGENTS.md` and `CLAUDE.md` now explicitly warn that Panels `chat_messages` and `chat_turns` are
  product-visible UI/audit state, not the worker's Hermes conversation.
- `docs/chat.md`, `docs/systems.md`, and `docs/tickets-and-gates.md` now state that worker-visible
  guidance must go through the Hermes gateway/session path or actual worker prompt; a visible chat row
  can mirror delivery but is not delivery by itself.
- The `panels-ticket-workflow` skill now carries the same boundary so planning agents do not propose
  "append to chat" as a worker-awareness mechanism. The generic `panels-worker` role skill was left
  free of this project-specific guidance.
- `decisions.md` records the owner correction as D34 and corrects the older D31 wording.

Verification status:

- Documentation/skill-only change; verified by searching for the new boundary language and corrected
  stale wording.

Immediate next step:

- None for this doc/guidance correction unless the owner wants the same warning added to another surface.

## Current work cycle (2026-07-09): Shared ticket screen in Workspace

Current implementation:

- Selecting a Workspace ticket now mounts the canonical `TicketRoute` in the right pane while the
  Workspace ticket rail remains visible.
- The ticket mount is keyed by ticket ID so selecting another ticket recreates its ID-bound
  resources; the standalone ticket route is unchanged.
- The Chief of Staff remains the default and return surface, and the embedded ticket uses scoped
  pane layout instead of a Workspace-specific ticket implementation.
- The obsolete selected-ticket link placeholder and its styles were removed, and the frontend doc
  now describes the shared screen behavior.

Verification status:

- Focused Svelte check, production build, and Workspace browser test passed; Svelte still reports
  the three existing `TicketRoute.svelte` initial-`id` warnings.
- Full `./verify` passed: ruff ok, mypy ok, 180 unit tests passed, build/frontend gates ok,
  36 e2e tests passed, `VERIFY: PASS`.

Immediate next step:

- Ticket `t_aejd0by7` is done after the result update.

## Current work cycle (2026-07-09): Ticket user notes and recap semantics

Current implementation:

- Tickets now expose a ticket-level `user_note` for preserved intake context / user guidance.
- Field-level notes have been clarified to `user_note` in contracts, codecs, API JSON, worker note writes,
  ticket copy text, and frontend typing, while legacy field JSON with `notes` still reads correctly.
- CLI support now includes `ticket create --user-note/--user-note-file`, `ticket set user-note`, and
  worker note writes as field user guidance.
- The Ticket screen renders an editable User note block above Recap, and the recap placeholder now frames
  recap as short cold-reader orientation.
- Seed import moves legacy ticket body/context into the ticket-level user note instead of success notes.
- Worker skills and docs now distinguish ticket user notes, field user notes, and recaps.

Verification status:

- Focused backend/seed/value tests passed: `.venv/bin/python -m pytest tests/unit/test_tickets_engine.py
  tests/unit/test_seed.py tests/unit/test_value_edit_logic.py -q`.
- Focused CLI/UI tests passed: `.venv/bin/python -m pytest
  tests/e2e/test_cli_verbs.py::test_ticket_approval_copy_events_and_worker_note_shape
  tests/e2e/test_flows_a.py::test_ticket_user_note_renders_as_own_intake_block -q`.
- Full `./verify` passed after wrapping the two new long CLI lines: ruff ok, mypy ok, 180 unit tests
  passed, build/frontend gates ok, 36 e2e tests passed, `VERIFY: PASS`.

Immediate next step:

- Result proposed on ticket `t_g8zgpn77`; ready for owner review.

## Current work cycle (2026-07-09): Worker completion chat rationale

Current implementation:

- `skills/panels-worker/SKILL.md` now tells workers to use the chat reply after a gated-field
  proposal for a brief rationale: why the proposal was shaped that way, what user direction or
  source facts mattered, and any real alternatives considered.
- The guidance explicitly keeps that rationale separate from the formal proposal and tells workers
  not to repeat field names, readiness, or approval/status details already shown by the UI.

Verification status:

- Focused text assertion passed: the worker skill contains the required rationale, separation,
  non-readiness-announcement, and non-status-repeat guidance.
- `.venv/bin/python -m pytest tests/unit/test_minds.py::test_provision_planner_home_skills_symlinks_repo_skills -q`
  passed.
- Full `./verify` passed after wrapping two pre-existing long CLI lines surfaced by ruff: ruff ok,
  mypy ok, 180 unit tests passed, build/frontend gates ok, 36 e2e tests passed, `VERIFY: PASS`.

Immediate next step:

- Propose the result on ticket `t_8qk5jfxh`.

## Current work cycle (2026-07-09): Pause active chat turn

Current implementation:

- Chat now exposes `POST /api/chat/{entity_id}/pause` for human-only interruption of the visible
  active chat turn.
- Pause calls the shared Hermes gateway `session.interrupt`, settles the `chat_turns` row as
  `interrupted`, preserves partial output as the final visible assistant/system line, and leaves
  `tickets.ticket_status` untouched.
- `ChatPanel` turns the composer send button into the Pause button while a turn is active, preserving
  the user's draft and refreshing chat state after pause.
- The live chat doc now states that pause is chat/session state, not ticket dispatch ownership.

Verification status:

- Targeted `.venv/bin/python -m pytest tests/unit/test_chat_seed.py tests/unit/test_chat_commands.py
  tests/unit/test_system_b.py` passed: 62 tests passed, 1 existing Starlette/httpx warning.
- Targeted `.venv/bin/python -m pytest tests/e2e/test_live_chat_state.py` passed: 5 tests passed.
- Full `./verify` passed: ruff ok, mypy ok, 179 unit tests passed, build/frontend gates ok, 35 e2e
  tests passed, `VERIFY: PASS`.

Immediate next step:

- Ticket `t_wyxgs2u0` now shows `done` after the corrected result update.

## Current work cycle (2026-07-09): Workspace status dropdown and hide-done toggle

Current implementation:

- Workspace status filters now render as a compact dropdown using the existing `ticket_status`
  choices.
- A separate Hide done checkbox filters workflow-state `done` tickets without changing the selected
  ticket-status filter.
- The redundant `Ticket status` subheader was removed so the row is just the dropdown and Hide done
  toggle under `Filters`.
- Workspace e2e coverage now exercises the dropdown path and hide/restore behavior for done tickets.
- `docs/frontend.md` describes the separate hide-done control.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` warnings.
- `.venv/bin/pytest tests/e2e/test_board_stage_indicators.py -q` passed: 2 tests passed.
- Full `./verify` passed: ruff ok, mypy ok, 178 unit tests passed, build/frontend gates ok,
  34 e2e tests passed, `VERIFY: PASS`.

Immediate next step:

- Result proposed on ticket `t_37fx0wvp`; ready for owner review.

## Superseded work cycle (2026-07-09): Review queue send-back guidance

The original send-back implementation in this cycle has been replaced by the direct Review rejection
worker turn described at the top of this file. Rejection no longer writes ticket chat, changes ticket
stage, emits `approval_returned`, or relies on a later System A prompt.

## Current work cycle (2026-07-09): Default approval onward scope to propose

Current implementation:

- `ScopePairPicker` now defaults a fresh gated approval to the next stage with `then propose`.
- The existing e2e approval flow now expects the proposed default and verifies the saved `at_cap` is
  `propose`.
- `docs/tickets-and-gates.md` now describes fresh approvals starting on `then propose` while keeping
  the human's ability to switch to `then stop`.

Verification status:

- `npm --prefix web run check && npm --prefix web run build && .venv/bin/python -m pytest
  tests/e2e/test_flows_a.py::test_e24_accept_in_review -q` passed. Svelte still reports the existing
  three `TicketRoute.svelte` initial-`id` warnings.
- Full `./verify` passed ruff, mypy, unit tests (175 passed), compile/frontend gates, and 32/33 e2e
  tests, then failed `tests/e2e/test_board_stage_indicators.py::test_workspace_groups_by_project_orders_by_progress_and_filters_status`
  waiting for `[data-status-filter="errored"]`. That failure is outside this ticket's changed files.

Immediate next step:

- Result proposed on ticket `t_pw57w861`; ready for owner review, with the unrelated full-verify
  failure called out.

## Current work cycle (2026-07-09): Chief chat richer activity status

Current implementation:

- Gateway prompt streams now surface tool/command activity as `ChatStreamChunk(type="activity")`.
- Server-owned human chat turns persist activity chunks into `chat_turns.phase='doing'` and
  `activity_label`, using the same active-turn state path as ticket worker activity.
- `ChatPanel` now labels running turns from the active turn's activity label, with clearer fallbacks
  for queued/working/responding/thinking, and exposes the pending activity text for browser checks.
- Added coverage proving Chief of Staff chat shows a running turn's partial assistant output, live
  activity label, updated activity label, disabled send state, and remount behavior.

Verification status:

- `.venv/bin/pytest tests/unit/test_chat_seed.py::test_chat_state_shows_active_turn_activity_label
  tests/e2e/test_live_chat_state.py::test_chief_chat_shows_running_activity_status_after_remount
  -q` passed after rebuilding `web/dist`.
- `.venv/bin/pytest tests/unit/test_chat_seed.py tests/e2e/test_live_chat_state.py -q` passed:
  35 passed, 1 existing Starlette/httpx warning.
- Full `./verify` passed: ruff ok, mypy ok, 175 unit tests passed, build/frontend gates ok, 33 e2e
  tests passed, `VERIFY: PASS`.

Immediate next step:

- Result proposed on ticket `t_u10mmpj1`; ready for owner review.

## Current work cycle (2026-07-09): Chat panels only scroll on initial load

Current implementation:

- The shared `ChatPanel` waits for the initial chat state to finish loading, scrolls to the latest
  message once after rendering, and never changes scroll position for later transcript, pending,
  or streamed-output updates.
- The behavior is centralized in `ChatPanel`, so ticket, workspace, and Chief of Staff chat panels
  inherit it without route-specific layout changes.
- Browser coverage proves both initial bottom positioning and preserved user scroll during a pending
  turn with partial streamed output, as well as after a completed reply.

Verification status:

- `npm --prefix web run check`, `npm --prefix web run build`, and the focused chat browser test
  passed before unrelated concurrent Workspace edits made `BoardRoute.svelte` invalid. The three
  existing `TicketRoute.svelte` initial-`id` warnings remain.
- Codex's first review found missing pending/streaming coverage; the slow-fake browser path now proves
  that case, and the follow-up review returned `RESOLVED`.
- Full `./verify` passed ruff, mypy, 181 unit tests, asset/build checks, and all 36 e2e tests, but the
  overall frontend gate failed on an unrelated concurrent `BoardRoute.svelte` `{@const}` placement
  error. That file is outside this ticket and continued changing during verification.

Immediate next step:

- Result proposed on ticket `t_7sbe2vay`; ready for owner review. Rerun full verification after the
  concurrent Workspace change settles.

## Current orchestration ledger (2026-07-09)

Important wording: an agent being "complete" only means the agent submitted a deliverable. It does
not mean the work is accepted, integrated, or owner-reviewed.

Orchestration cleanup:

- Owner asked to keep the `orchestration/` structure but remove unnecessary bulk.
- Deleted completed per-ticket pipeline artifacts under `orchestration/tickets/`, keeping each
  ticket directory and its `ticket.md` scope file.
- Deleted old dogfood evidence logs, verify-run transcripts, top-level audit review artifacts,
  dogfood report, and the stale per-ticket orchestrator playbook.
- Preserved redesign/design intent folders, current targeted plans, chief-of-staff draft,
  repo-guidance report, and the live-chat accuracy plan.

Outstanding planned / submitted work:

- **In-place markdown editing** — Schrodinger submitted an implementation in the main worktree:
  `web/src/lib/markdownEdit.ts`, `web/src/components/InlineEdit.svelte`,
  `web/src/components/ApprovalBlock.svelte`, `assets/app.css`,
  `tests/e2e/test_flows_a.py`, `web/dist/*`, plus memory files. Agent-reported checks passed and
  an agent-run Codex review said `NO VIOLATIONS`, but the lead has not reviewed or accepted the
  diff yet. Treat as **submitted, pending lead review/integration**.
- **Derived sprint item status** — Dewey's worktree has been merged into the main integration
  branch. Sprint items no longer store or accept explicit status/status-proposal writes; status is
  derived on read from child tickets and blocking links. Focused unit/web checks passed during
  integration; full `./verify` is still deferred until the end-of-batch verification.
- **Chief-of-staff agent** — first slice is implemented on main: repo role skill, planner-home
  symlink provisioning, top-level chat entity, and full-pane `#/chief` route. Browser verification
  passed after restarting the stale server.
- **Repo guidance / `AGENTS.md` + `CLAUDE.md`** — Descartes submitted findings at
  `orchestration/repo-guidance/descartes-report.md`. Owner decision: do **not** directly edit
  `AGENTS.md` / `CLAUDE.md` further yet. This belongs in a planned guidance cleanup, including a
  correct map of repo-local Panels role skills, planner-home symlinked skills, and user/Hermes
  workflow skills.
- **Hermes skill inventory** — Ampere is currently exploring drafted and installed skills. The task
  is to locate Panels/chief-of-staff/rollover/sprint-planning/worker skills, record exact
  frontmatter names and paths, and call out wrong or ambiguous names such as repo references to
  `panels-rollover` versus installed Hermes `rollover`.
- **Live chat accuracy / server-owned live turn state** — implemented on main. Panels now owns
  `ChatState` through `chat_messages` and `chat_turns`; ticket chat, worker turns, and Chief of
  Staff chat read the same state shape. Browser-driven remount checks and full `./verify` pass.

Plan files / artifacts that must stay on the radar:

- `orchestration/chief-of-staff/erdos-skill-draft.md`
- `orchestration/repo-guidance/descartes-report.md`
- Dewey's derived-status work has been merged; keep the worktree only until the merge commit is
  accepted.
- In-place markdown editing is committed on main.
- Russell live-chat plan is recorded at `orchestration/live-chat-accuracy/plan.md`.
- Goodall live-chat implementation is complete on main.

## Current work cycle (2026-07-09): Project summary context field

Owner correction: project context is one free-text `summary` field only. Repo locations, when useful,
belong inside that summary text; there is no separate repo-location field.

Current implementation:

- Added `projects.summary` with empty-string defaults for fresh schemas and existing databases.
- Project API JSON now includes `summary`; project creation accepts optional summary text.
- Added human-only `PATCH /api/projects/{project_id}` for `name` and `summary`, with
  `project_updated` events invalidating the `projects` resource.
- Added CLI support: `panels project create --summary ...` and
  `panels project set <project_id> summary --value/--body-file/--clear`.
- Updated frontend project typing and `docs/projects.md` for the single summary field.

Verification status:

- `.venv/bin/python -m py_compile src/planner/core/db.py src/planner/core/contracts.py
  src/planner/projects/contracts.py src/planner/projects/data.py src/planner/projects/api.py
  src/planner/cli/main.py` passed.
- `.venv/bin/python -m pytest tests/unit/test_db.py tests/unit/test_projects.py
  tests/unit/test_frontend_event_mapping.py` passed: 6 passed, 1 existing Starlette/httpx warning.
- Full `./verify` passed: ruff ok, mypy ok, 174 unit tests passed, build/frontend gates ok, 32 e2e
  tests passed, `VERIFY: PASS`.

Immediate next step:

- Result proposed on ticket `t_zd6ycvrw`; ready for owner review.

## Current work cycle (2026-07-09): Live chat accuracy / server-owned turn state

Owner request: implement the live chat accuracy plan on current main after the derived sprint item
status merge. The chat panel must accurately reflect user/system/worker/chief activity across live
streaming, polling, navigation/remount, and future thinking/doing indicators; no client-only
workarounds.

Current implementation:

- Added generic `chat_messages` and `chat_turns` tables. `chat_turns` enforces one running turn per
  entity with a partial unique index.
- Added chat state contracts: `ChatState`, `ChatStateMessage`, and `ChatTurn`.
- Added chat lifecycle events: `chat_message_recorded`, `chat_turn_started`, `chat_turn_updated`,
  and `chat_turn_finished`.
- Added `GET /api/chat/{entity_id}/state` as the UI source of truth.
- Added `POST /api/chat/{entity_id}/turns`, which starts a background server-owned human turn.
- Kept the existing `/history`, `/send`, `/stream`, and `/command` routes for compatibility.
- Wired System B worker steps into the same chat-state writer. Worker turns now record a worker
  visible line, session key, gateway deltas/activity, completion, and errors.
- Switched `ChatPanel` to render server `ChatState`. It no longer keeps an optimistic transcript or
  retries Hermes history based on `ticket_status`; it polls `/state` only while the server reports
  an active turn.
- Updated frontend event mapping so chat lifecycle events invalidate `chat:<entity_id>`, including
  `agent_panels_chief_of_staff`.
- Updated live docs and recorded D65/D66.

Verification status:

- `.venv/bin/python -m py_compile src/planner/chat/contracts.py src/planner/chat/data.py
  src/planner/chat/service.py src/planner/chat/api.py src/planner/core/db.py
  src/planner/core/contracts.py src/planner/runtime/system_b.py` passed.
- `.venv/bin/python -m pytest tests/unit/test_chat_seed.py tests/unit/test_chat_commands.py
  tests/unit/test_minds.py tests/unit/test_system_b.py tests/unit/test_db.py
  tests/unit/test_frontend_event_mapping.py` passed: 85 passed, 1 existing Starlette/httpx
  warning.
- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run build` passed with existing Vite asset warnings and the same Svelte
  warnings.
- Browser-driven evidence:
  - `test_ticket_chat_send_survives_navigation_from_server_state` sends in a ticket chat, asserts
    `/api/chat/{ticket_id}/state` contains the human/assistant messages, navigates away/back, and
    opens a fresh browser context to confirm the panel reloads them from the server.
  - `test_chief_chat_send_survives_navigation_from_server_state` repeats the same check for
    `agent_panels_chief_of_staff`.
  - `test_ticket_chat_shows_running_worker_turn_after_remount` seeds a running worker turn in the
    real server DB, asserts `/state` reports `origin=worker`, `phase=doing`, and the activity
    label, then verifies the ticket chat still shows the worker line, activity text, and pending
    indicator after navigation/remount.
- Focused browser command:
  `.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_e26_chat_panel_echo_and_offline
  tests/e2e/test_flows_a.py::test_slash_menu_runs_skill
  tests/e2e/test_flows_a.py::test_slash_menu_runs_display_command
  tests/e2e/test_chief_of_staff.py tests/e2e/test_live_chat_state.py` passed: 7 passed.
- Full `./verify` passed: ruff ok, mypy ok, unit suite ok, build check ok, frontend ok, e2e suite
  ok (`VERIFY: PASS`).

Immediate next step:

- Ready for owner review/commit. No derived sprint item status writes were reintroduced.

## Current work cycle (2026-07-09): Workspace filter divider cleanup

Owner correction: the Ticket UI project edit path does work. Remove the extra divider line between
the Workspace filters and the project ticket sections.

Current implementation:

- Removed the `border-bottom` from `.board-workspace-filters`.
- Reverted the speculative copy-text and regression-test edits from the false-alarm investigation,
  keeping the work scoped to the sidebar visual cleanup.

Verification status:

- `git diff --check` passed.
- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte`
  initial-`id` warnings.
- `.venv/bin/python -m pytest tests/e2e/test_board_stage_indicators.py` passed: 2 passed.
- Full `./verify` was attempted twice after this cleanup and did not get a clean run:
  - First run failed in e2e because one `panels serve` subprocess did not answer `/api/meta` within
    the 15s test boot budget, even though Uvicorn had started.
  - Second run passed ruff, mypy, unit, build, frontend, and 31/32 e2e tests, then failed the
    existing chat reload test `test_e26_chat_panel_echo_and_offline` waiting for the human chat
    line after reload.
  - Neither failure touched the Workspace sidebar/filter surface changed here.

Immediate next step:

- Ready for owner review of the divider cleanup; full verify is not clean because of unrelated e2e
  failures.

## Current work cycle (2026-07-09): Workspace sidebar project grouping

Owner request: widen the Workspace left sidebar, group tickets by project instead of stage,
reintroduce four progress circles per ticket row, and add ticket-status filters below Chief of
Staff.

Current implementation:

- Implemented directly because the change is cohesive across the board read
  projection, one Svelte route, shared CSS, and focused tests; splitting it would create overlapping
  edits in the same files.
- Backend board cards now keep `project_id` / `project` unchanged and add `group_project_id` /
  `group_project`. Standalone tickets group by their own project; parented tickets group by the
  parent sprint item's project; missing project groups as `No project`.
- Workspace route now derives project sections, sorts rows by ticket progress within project, adds
  a single-select `ticket_status` filter, and renders all four stage dots.
- Docs are updated with the new Workspace/project behavior.
- `web/dist` has been rebuilt so the FastAPI-served app uses the new Workspace bundle.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte`
  initial-`id` warnings.
- `.venv/bin/python -m pytest tests/unit/test_board_view.py` passed: 2 passed.
- Focused e2e passed:
  `.venv/bin/python -m pytest tests/e2e/test_board_stage_indicators.py
  tests/e2e/test_chief_of_staff.py tests/e2e/test_flows_a.py::test_e22_cli_create_live_board
  tests/e2e/test_flows_b.py::test_e31_refresh_restores_state` → 7 passed.
- Full `./verify` passed: ruff ok, mypy ok, 173 unit tests passed, build/frontend gates ok, 32 e2e
  tests passed, `VERIFY: PASS`.

Immediate next step:

- Ready for owner review.

## Current work cycle (2026-07-09): Derived sprint item status integration

Owner request: commit the current main work, merge Dewey's derived sprint-item status work, then set
Goodall off on main.

Current implementation:

- Sprint item rows store only plain item fields and sprint placement.
- `ItemStatus` is now `todo`, `in_progress`, `blocked`, `done`, derived in
  `planner.sprints.logic.status` from child tickets, runtime ticket status, and blocking links.
- Legacy `status = deferred_next_sprint` rows migrate to backlog placement (`sprint_id = NULL`).
- Legacy `blocked_by` JSON migrates into `links(kind='blocks')`.
- Item status proposal routes and Review item-status approval UI are removed.
- Seed import recognizes legacy tracking sections, but does not persist them as item status; legacy
  `Deferred` imports as backlog.

Verification status:

- `.venv/bin/python -m py_compile ...` passed for the edited backend modules.
- `.venv/bin/python -m pytest tests/unit/test_db.py tests/unit/test_sprints.py
  tests/unit/test_seed.py tests/unit/test_authctx_routes.py` passed: 28 passed, 1 existing
  Starlette/httpx warning.
- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run test` passed.
- Full `./verify` has not been run for this merge yet.

Immediate next step:

- Regenerate `web/dist`, stage the merge, commit it, then instruct Goodall to begin live-chat
  implementation on main.

## Current work cycle (2026-07-09): Shared ticket stage component

Owner request: make Recap and Notes share a slightly larger header/body treatment, extract that
treatment into one component also used on Ticket detail, make the approval payload inside the
recessed Review surface use the same collapsible header/content shape, and move Review-only Skip /
Open Ticket actions outside the recessed approval surface. Follow-up correction: the product concept
must be one ticket-stage component used for completed stages, current stages, and stages awaiting
approval; lower-level primitives may still be shared internally.

Result:

- Added `TicketStageSection.svelte` as the stage-level orchestrator for ticket stages.
- Added `ContentDisclosure.svelte` as the shared collapsible header/body component.
- `TicketRoute` now loops stages and renders `TicketStageSection`; it no longer separately assembles
  top-level `ApprovalBlock`, `CollapsibleField`, notes, values, and proposals.
- `ReviewRoute` now renders the same `TicketStageSection` for ticket-stage approvals; only status
  approvals keep their separate non-ticket branch.
- `TicketStageSection` composes `CollapsibleField`, `ApprovalBlock`, `ContentDisclosure`,
  `InlineEdit`, and `ProposalCard` internally.
- `ApprovalBlock` now renders the approval payload with the shared collapsible header. In approval
  layout, the Approve controls sit at the bottom of the recessed approval surface.
- Active approval/final-review stage rows open by default on Ticket detail, so the in-place approval
  editor remains visible after moving it into the stage row.
- Review's Skip and Open Ticket controls now render as their own outside action row.
- Ticket detail now uses the same component for the top Recap block and field Notes under stage
  content.
- Removed the old one-off Review notes and Ticket note CSS paths.
- Updated the selected minimal Review mock HTML to match the new component structure.
- Implemented directly rather than dispatching a sub-agent because the current request did not ask
  for delegation, and the change crossed tightly coupled Svelte/CSS layout files.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run build` passed with the existing Vite runtime-asset warnings and the same
  three Svelte warnings. This regenerated `web/dist`.
- `python3` HTMLParser accepted
  `orchestration/review-redesign/present-state-minimal-options/selected-direction.html`.
- `.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_e23_env_pinned_propose
  tests/e2e/test_flows_a.py::test_e24_accept_in_review
  tests/e2e/test_flows_a.py::test_e25_edit_accept_in_review
  tests/e2e/test_flows_b.py::test_e30_review_approve_to_done` passed.
- `git diff --check` on the scoped frontend/CSS/mock files passed.
- Did not run full `./verify`.

Immediate next step:

- Owner visual review in the running Panels server.

## Current work cycle (2026-07-09): Chief-of-staff first slice

Owner request: add the `panels-chief-of-staff` repo role skill from the Erdos draft, provision it
into planner Hermes home, expose a full-pane Chief of Staff chat route, and support its stable
top-level chat entity without implementing the later server-owned live-turn architecture.

Current implementation:

- Added `skills/panels-chief-of-staff/SKILL.md` from
  `orchestration/chief-of-staff/erdos-skill-draft.md` without the outer markdown fence.
- Added `agent_panels_chief_of_staff` as the one supported top-level agent chat entity, backed by
  `agent_chat_sessions`, so chat history/send/stream/command can persist the same durable
  `chat_session_key` without a ticket or day id.
- Added `EntityRoutingGateway` and production server wiring so chief chat traffic uses a separate
  shared gateway child with `HERMES_TUI_SKILLS=panels-chief-of-staff`, while ticket/day chat and
  System B keep the worker gateway.
- Added the `panels-chief-of-staff` skill to planner Hermes home provisioning.
- Added a full-pane `#/chief` route and primary nav entry using the existing chat thread/composer
  visual language, with a Chief of Staff label and placeholder.
- Added focused tests for skill provisioning, chief chat persistence/history/not-found boundary,
  and the Chief route/nav/chat smoke.

Verification status:

- `.venv/bin/python -m py_compile src/planner/chat/service.py src/planner/core/db.py
  src/planner/minds/config.py src/planner/core/server.py src/planner/chat/api.py
  src/planner/minds/shared_gateway.py tests/unit/test_chat_seed.py tests/unit/test_minds.py`
  passed.
- `.venv/bin/python -m pytest tests/unit/test_chat_seed.py tests/unit/test_minds.py
  tests/unit/test_db.py` passed: 52 passed, 1 existing Starlette/httpx deprecation warning.
- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run build` passed with the existing Vite runtime-asset warnings and the same
  three Svelte warnings. This regenerated `web/dist`.
- `.venv/bin/python -m pytest tests/e2e/test_chief_of_staff.py` passed.
- `git diff --check` passed.
- Independent `codex exec --model gpt-5.5 --sandbox read-only` review first found the chief entity
  would still run under the worker skill; after the gateway-router fix, the scoped rerun returned
  `NO VIOLATIONS`.

Immediate next step:

- Ready for owner review. Full `./verify` was not run for this bounded slice.

## Current work cycle (2026-07-09): Chief-of-staff browser verification

Owner reported a browser bug: opening/using Chief of Staff produced `not_found` with message
`no chattable entity for id`.

Result:

- Reproduced the bug on the already-running `127.0.0.1:8767` server:
  `GET /api/chat/agent_panels_chief_of_staff/history` and
  `POST /api/chat/agent_panels_chief_of_staff/send` both returned 404
  `{"code":"not_found","message":"no chattable entity for id"}`.
- Identified the cause: the `8767` server process was started before the chief backend code was
  loaded. It served the new static bundle from disk, but its in-memory Python service still had the
  old chat resolver and the live DB had no `agent_chat_sessions` table yet.
- Confirmed the current worktree code on a fresh isolated server resolved the same entity:
  `GET /api/chat/agent_panels_chief_of_staff/history` returned 200 with empty history, and
  `POST /api/chat/agent_panels_chief_of_staff/send` returned 200 with the fake echo gateway.
- Restarted the real Panels server on `127.0.0.1:8767` from `.venv/bin/panels serve`. The same live
  endpoint now returns 200 and the live `data/planning.db` has `agent_chat_sessions`.
- Browser-drove `#/chief` on `8767`: primary nav exposes `Chief of Staff`, route renders
  `section[data-screen="chief"]`, chat is inside the full-pane `.chief-chat-shell` and not a ticket
  `.chat-rail`, composer is visible with `Message Chief of Staff...`, typing enables the send
  control, and no console/page/request failures were recorded.
- Browser-drove existing `#/day` and `#/ticket/t_3x59papc` on `8767`; both rendered, and ticket chat
  still uses the ticket side rail with the employee composer placeholder.
- Browser-drove an actual chief chat send on the isolated fake-gateway server; `POST
  /api/chat/agent_panels_chief_of_staff/stream` returned 200 and the page rendered
  `echo: browser send check`, with no console/page/request failures.

Verification status:

- Live API before restart: 404 reproduced on `/api/chat/agent_panels_chief_of_staff/history` and
  `/api/chat/agent_panels_chief_of_staff/send`.
- Fresh current-worktree API: both endpoints returned 200 on isolated server `127.0.0.1:8799`.
- Restarted live API: `GET /api/chat/agent_panels_chief_of_staff/history` returned 200 on
  `127.0.0.1:8767`.
- Playwright browser verification passed on `8767` for Chief route/nav/full-pane layout/composer,
  Day route, Ticket route, and zero console/page/request failures. Screenshots saved under
  `data/chief-browser-verification/`.
- Playwright browser send passed on isolated fake-gateway server.
- `.venv/bin/python -m pytest tests/e2e/test_chief_of_staff.py` passed.
- `git diff --check` passed.

Immediate next step:

- Ready for owner review. The real Panels server is running on `127.0.0.1:8767` from the current
  worktree.

## Current work cycle (2026-07-08): Review approval UI QA and scoped fixes

Owner request: QA the just-completed Review approval UI and ticket field-circle encoding, patching
only small obvious integration issues in the owned frontend/CSS files, with no backend/API changes
and no full `./verify`. Follow-up owner correction: Review notes belong below the recap at the top
of the page, before the recessed approval payload, and the text sizing/spacing hierarchy needed a
pass.

Result:

- Reviewed `ReviewRoute`, `ApprovalBlock`, `ScopePairPicker`, `ui.ts`, `CollapsibleField`,
  `TicketRoute`, `assets/app.css`, and the focused Review e2e tests.
- Fixed `ScopePairPicker` so an external bound `scope = null` reset (as `ApprovalBlock` does when a
  new proposal body arrives) deterministically restores the default next-stage + `stop` scope,
  instead of preserving a previous same-stage approval choice or leaving the approval disabled.
- Added the generic `data-accept` selector to status approval's button while keeping the existing
  `data-accept-status` selector.
- Moved Review notes into the top context stack directly under recap: title, recap, collapsed notes,
  then the separate recessed proposal and actions.
- Tightened Review hierarchy spacing and text sizing so title/recap/notes read as one top context
  block and the proposal reads as the only approval object.
- Removed the extra dropdown chevrons from the inline approval scope selectors; the underline is the
  affordance. Selector text now uses `--text-default` rather than the higher-emphasis
  `--text-strong`.
- Updated the selected minimal Review mockup HTML to match the product structure.
- Confirmed ticket field circles are driven by `fieldStageVisualState(detail, fieldName)` from
  lifecycle state/proposal/running status, not by stored field text.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run build` passed with the existing Vite runtime-asset warnings and the same
  three Svelte warnings. This regenerated `web/dist`.
- `python3` HTMLParser accepted
  `orchestration/review-redesign/present-state-minimal-options/selected-direction.html`.
- `.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_e24_accept_in_review
  tests/e2e/test_flows_a.py::test_e25_edit_accept_in_review
  tests/e2e/test_flows_b.py::test_e30_review_approve_to_done` passed.
- `git diff --check` on the scoped frontend/CSS files passed.
- Did not run full `./verify`, per owner instruction.

Immediate next step:

- Ready for owner review.

## Current work cycle (2026-07-08): Segment 4 stage-circle encoding

Owner request: implement approved Segment 4 ticket field circle states without backend/API changes,
without touching Review UI files, and without inferring field completion from incidental text.

Result:

- Added a pure `fieldStageVisualState(detail, fieldName)` frontend derivation over existing
  `TicketDetail` data.
- Changed `CollapsibleField` from glyph input to semantic `stageState`, with `data-stage-state` on
  each field section.
- Updated Ticket field rendering to pass lifecycle-derived stage state instead of local mark glyphs.
- Reworked only `.fsec-mark` field-circle CSS for completed/current/upcoming states, including
  reduced-motion handling for the running spinner.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run build` passed with the same existing Svelte warnings and Vite runtime-asset
  warnings.
- `git diff --check` on the scoped source/CSS files passed.
- Did not run full `./verify`, per owner instruction for this slice.

Immediate next step:

- Ready for owner review.

## Current work cycle (2026-07-08): Review approval-present selected direction

Owner request: implement the selected minimal Review approval-present direction in product code.

Current implementation:

- Review approval-present now renders only the ticket title, recap, proposal, collapsed notes when
  notes exist, and actions.
- The proposal is the only sunken/recessed surface and no longer has a border in the Review layout.
- Notes moved below the proposal into a closed-by-default disclosure; no divider is rendered before
  notes or actions.
- Actions split Skip on the left from the approval controls on the right.
- `ScopePairPicker` is now the shared inline `until` stage selector plus `then` stop/propose selector,
  defaulting to next stage and then stop.
- Rebuilt `web/dist` because the e2e harness serves the production bundle via `panels serve`.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run build` passed with the existing Vite runtime-asset warnings and the same
  `TicketRoute.svelte` warnings.
- `.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_e24_accept_in_review
  tests/e2e/test_flows_a.py::test_e25_edit_accept_in_review
  tests/e2e/test_flows_b.py::test_e30_review_approve_to_done` passed.

Immediate next step:

- Ready for owner review; stage-circle encoding remains out of scope.

## Current work cycle (2026-07-08): Segment 1 low-risk ticket UI cleanup

Owner request: remove the Ticket header `auto` pill for now, replace awkward Ticket field `(none)`
empty text, and make notes read as plain supporting text rather than recessed approval payloads.

Result:

- Removed the Ticket header `auto` pill and its derived status helper/resource code from
  `TicketRoute.svelte`.
- Kept `MarkdownBlock`'s default placeholder unchanged for other screens, and passed Ticket-only
  empty copy for recap/stage fields.
- Changed the Ticket header's empty sprint label from `(none)` to `no sprint`.
- Made `.note` plain supporting text in `assets/app.css`; the approval surface remains on
  `.approval`.
- Updated the one e2e wait that asserted `[data-auto-run-status]`.

Verification status:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` initial-`id`
  warnings.
- `npm --prefix web run build` passed with the same existing Svelte warnings and Vite runtime-asset
  warnings.
- `.venv/bin/python -m pytest tests/e2e/test_flows_a.py::test_e26_chat_panel_echo_and_offline`
  passed.

Immediate next step:

- None for this Segment 1 cleanup.

## Current work cycle (2026-07-08): Review empty-state design options

Owner request: implement the Review empty state chosen from the horizon mockup.

Current implementation:

- Replaced the bare Review empty text with a centered horizon-style empty state while preserving
  `data-review-empty` for e2e selectors.
- The main empty-state text is exactly: `There is nothing to review right now.`
- Added `running_agents` to `/api/queues`, counted from tickets whose `ticket_status` is
  `agent_running_step`, and rendered it as tertiary empty-state text.
- Kept the present-approval Review layout unchanged.
- Implemented directly because the slice is small and tightly scoped to the named files.

Verification status:

- `npm --prefix web run check` passed with 0 errors and the three existing
  `TicketRoute.svelte` initial-`id` warnings.
- `.venv/bin/python -m ruff check src/planner/tickets/views.py` passed.
- A focused venv Python probe of `queues_view` passed, returning
  `{'approvals': [], 'overdue': [], 'running_agents': 1}` for one running ticket.
- `git diff --check` on the touched files passed.

Immediate next step:

- Ready for owner review.

## Prior work cycle (2026-07-08): Review empty-state design options

Owner note: the Review empty state should be a designed centered waiting state, not a bare
"nothing waiting" line. It should create calm, communicate that there is nothing to review, and may
eventually show how many agents are running.

Additional owner dogfood notes to keep in mind for the ticket/review redesign:

- Ticket empty values shown as `(none)` feel odd, including stage fields.
- Recessed visual treatment should represent the approval payload only; notes should not look
  recessed.
- Stage circles should encode state: completed = solid green; current running = rotating outline;
  current waiting = outline; current awaiting approval = solid accent/orange.
- Inline editing should preserve rendered structure in place; bullets and spacing should not collapse
  into a plain textarea experience.
- Changing a ticket's approval/scope can start an agent immediately, but the chat pane currently
  gives weak live visibility while the run is in progress. The owner expects clearer in-progress
  feedback.
- Ticket header `ticket_status` and derived `auto` status read as duplicated even though they are
  different concepts; owner says the `auto` pill can go for now.
- Approval scope should feel like one sentence/action: approve until the next field, then stop by
  default.
- Review page needs a UX pass when an approval is present: the current approval surface has double
  card/layering treatment. The approval-scope behavior fix belongs with the later approval work, but
  the visual layering issue is its own review-page redesign item.
- Owner review of Segment 3 mockups: the split-pane/sidebar direction is interesting but wrong for
  Review. Review should become calmer and lower-information, not add context panels or extra things
  to read. The approval space only needs to communicate the small amount required to make the
  decision.
- Hard rule for Review approval-present state: only show ticket title, recap, proposal, collapsed
  notes, and actions. If information does not help approve the ticket, it is unnecessary on this
  screen.
- Sprint item status may be the wrong model as an explicit editable field. Owner expects it to be
  derived from child tickets: done when all tickets are done, in progress when any ticket is in
  progress, etc. This needs a backend/API/frontend model pass, not just copy or styling.

Segmentation decision from first dogfood pass:

- Segment 1: low-risk Ticket UI cleanup — remove `auto`, improve empty field display, make notes
  non-recessed, reserve recessed treatment for approval payloads.
- Segment 2: approval action/scope UX — make approval read as one action: approve until next field,
  then stop by default. Defer until after Segment 1 lands.
- Segment 3: Review present-approval layout pass — remove double card/layering and improve the
  approval-present state. First step is static `agy` mockups for owner review.
- Segment 4: stage-circle encoding — planning pass first; completed/current/running/awaiting
  approval need distinct circle states.
- Later: live agent visibility, true in-place markdown editing, and derived sprint-item status.

Dispatch results:

- Worker `Singer` implemented the horizon-style Review empty state, including the queue-level
  `running_agents` count.
- Worker `Aquinas` implemented Segment 1 Ticket UI cleanup.
- Explorer `Galileo` produced a read-only implementation plan for Segment 4 stage-circle encoding.
- `agy` Gemini Flash generated static Segment 3 Review present-approval mockups under
  `orchestration/review-redesign/present-state-options/`.
- Follow-up owner correction: Segment 4 does not need a backend/API shape. The circle visual state
  should be derived by one pure function from already-loaded ticket/stage state, then passed into the
  field component.
- Owner approved the corrected Segment 4 plan. Implementation should add one pure frontend
  derivation for ticket field circle state, pass that semantic state into `CollapsibleField`, and
  render circles as: completed = solid green; current running = rotating accent outline; current
  waiting = plain outline; current awaiting approval = solid accent; upcoming = faint outline. Do
  not infer completion from incidental field text; use lifecycle position.
- Follow-up `agy` round generated calmer Segment 3 Review-present mockups under
  `orchestration/review-redesign/present-state-calm-options/`: no sidebar, no context dashboard, no
  timeline, only the information needed for the approval decision. The files parse with Python's
  stdlib `HTMLParser`.
- Third `agy` round generated strict minimal Segment 3 mockups under
  `orchestration/review-redesign/present-state-minimal-options/`. The variant files use only the
  five approved visible elements: ticket title, recap, proposal, collapsed notes, and actions. Hidden
  generated banners/scripts were removed after generation. The files parse with Python's stdlib
  `HTMLParser`, and a text scan found no variant references to sidebar/timeline/metadata/status/
  project/priority/deadline/queue concepts.
- Owner selected the Variant 01/flat-document direction with changes: the proposal should be
  recessed because it is the thing being approved, no divider line is needed, Skip belongs on the
  left, and the right action should use the future shared approval control shape: "Approve until X,
  then Y." Added `selected-direction.html` under the minimal options folder and linked it from that
  folder's `index.html`.
- Owner correction: the approval control cannot be one button. The selected direction now shows
  Skip on the left, then separate selectors for "Approve until" stage and "then" behavior, plus an
  Approve button on the right. This should map to the future shared approval-scope component.

Result:

- Added static mockups under `orchestration/review-redesign/empty-state-options/`.
- The comparison index links to three self-contained variants:
  - `pulse.html` — a quiet centered pulse line with "Nothing needs review" and agent count.
  - `telemetry.html` — a small agent activity diagram with active/waiting/blocked counts.
  - `horizon.html` — a softer waiting state focused on the next proposal.
- Used `agy` with Gemini Flash for initial concept prompts, then wrote the repo artifacts directly.
- No product code changed.

Verification status:

- Parsed all four static HTML files with Python's stdlib `HTMLParser`.
- Did not run `./verify` because this is a static design artifact only.

Immediate next step:

- Await owner preference from the mockups before cutting an implementation ticket.

## Current work cycle (2026-07-08): First-class projects table

Owner request: implement the first-class projects plan so projects are their own table with stable
IDs, while existing project-name callers keep working during the transition.

Current implementation:

- Added `projects` with default rows for Vylo, Tribe, Learning, and Other.
- Migrated `sprint_items`, `tickets`, and `ideas` from string `project` columns to `project_id`
  foreign keys. Parented tickets keep `project_id` null.
- Added project domain helpers plus `GET /api/projects` and human-only `POST /api/projects`.
- Ticket, item, and idea create/patch/list paths accept preferred `project_id` and legacy `project`
  name, reject mismatches, and serialize both `project_id` and display-name `project`.
- Added CLI `panels project list/create`, `--project-id` selectors, frontend `projects` resources,
  and `project_created -> projects` event invalidation.
- Updated seed import and focused migration/API/CLI/frontend tests.
- Wrote this slice directly because no sub-agent dispatch tool is exposed in this Codex session;
  the plan's ticket boundaries were followed as local integration phases.

Current hypothesis:

- The remaining risk is full-suite fallout from project filtering and schema-version migration paths.

Verification status:

- Targeted checks passed: `tests/unit/test_projects.py`, compileall for changed sprint modules, and
  ruff over the patched API/view/test files.
- The required read-only Codex migration/API review found one real issue: `GET /api/ideas` ignored
  `project_id`/legacy `project` filters and mismatch validation. That was patched and covered by
  `tests/unit/test_projects.py`.
- Final `./verify` passed after the review fix: ruff, mypy, 168 unit tests, compile/static checks,
  Svelte check, web build, frontend event test, and 22 e2e tests all passed.
- Known warnings remain: the existing Starlette `TestClient` deprecation warning, the existing
  `TestClock` collection warnings, and the three existing `TicketRoute.svelte` initial-`id` capture
  warnings.

Immediate next step:

- Ready for owner review.

## Current work cycle (2026-07-08): CLI noun-shape implementation

Owner request: build the CLI redesign, then review it.

Current implementation:

- Rebuilt `src/planner/cli/main.py` around `day`, `ticket`, `sprint`, and `worker`.
- Product commands use headerless human requests through the CLI HTTP seam; worker commands keep
  `X-Plan-Actor`.
- Added `POST /api/tickets/{ticket_id}/propose`: it infers the current gating field and writes the
  required recap in the same ticket transaction.
- Added existing-ticket sprint-item placement routes:
  `POST /api/items/{item_id}/tickets` and
  `DELETE /api/items/{item_id}/tickets/{ticket_id}`.
- Updated e2e callers, unit coverage, docs, and local worker/planner skills to remove retired
  top-level command homes.
- Read-only subagent review found CLI shape mismatches: `ticket approve --next-ceiling`, missing
  `ticket list --sprint-item`, missing `ticket copy/events`, sprint add/remove using a positional
  sprint selector, `sprint set current` not resolving, and reversed `worker note` args. These were
  fixed.
- Read-only backend review found raw PATCH value leaks in ticket/item APIs. These were fixed by
  marshalling patch fields before they reach writers.

Current hypothesis:

- The backend now has the canonical operations needed by the CLI, so the command tree does not need
  client-side multi-write tricks.
- The most likely failures are grammar regressions in the e2e suite and static feedback in the
  rewritten CLI file.

Immediate next step:

Verification status:

- Fresh `./verify` passed after the reviewer fixes: ruff, mypy over 80 source files, 165 unit
  tests, compile/static checks, `npm --prefix web run check`, `npm --prefix web run build`,
  `npm --prefix web test`, and 21 e2e tests all passed.
- Known warnings remain: the three `TicketRoute.svelte` initial-`id` capture warnings and the
  existing Pytest `TestClock` collection warnings.

Immediate next step:

- None for this CLI slice; ready for owner review.

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

## Markdown in-place editing follow-up

Current build stage: source fix implemented and focused verification passed.

What just passed:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` warnings.
- `npm --prefix web run build` passed with the usual external asset warnings.
- `.venv/bin/pytest tests/e2e/test_flows_a.py::test_e23_env_pinned_propose
  tests/e2e/test_flows_a.py::test_e25_edit_accept_in_review -q` passed.
- `git diff --check -- web/src/lib/markdownEdit.ts web/src/components/InlineEdit.svelte
  web/src/components/ApprovalBlock.svelte assets/app.css tests/e2e/test_flows_a.py` passed.
- Read-only Codex diff review (`--model gpt-5.5 --sandbox read-only`) returned `NO VIOLATIONS`.

Current hypothesis: the true in-place markdown behavior is fixed through shared primitives.
`InlineEdit` and `ApprovalBlock` now keep the rendered markdown DOM on focus, serialize the
supported rendered DOM back to markdown on commit/approve, paste plaintext only, and preserve the
old non-markdown edit path.

Next step: final response should call out that `npm run build` rewrote `web/dist` generated files,
which were outside the requested source scope and already dirty in this shared worktree.

Blockers: none.

## Workspace rename + Chief of Staff right pane

Current build stage: implemented and verified.

What changed:

- `#/workspace` is now the canonical user-facing route, with `#/board` retained as a compatibility
  alias over the same Svelte route.
- The Workspace nav label replaces Board, while backend `/api/board` and the `board` resource key
  stay unchanged.
- The Workspace right pane defaults to the existing Chief of Staff chat. Selecting a ticket shows
  the current ticket link view, and the full-width borderless Chief of Staff button restores the
  chat.
- The Workspace rail now orders tickets by recent ticket activity inside each project and renders one
  current-stage/status dot at the far right of each ticket row, grouped by project with
  ticket-status filters in the left rail.
- Focused e2e coverage was updated/added for Workspace routing, the embedded Chief of Staff chat,
  legacy `#/board`, and the one-dot stage indicator.
- Follow-up: removed Chief of Staff from the top nav while keeping `#/chief` as a direct route.

What passed:

- `npm --prefix web run check` passed with the existing three `TicketRoute.svelte` warnings.
- `npm --prefix web run build` passed with the existing external-asset warnings and the same three
  `TicketRoute.svelte` warnings.
- `.venv/bin/pytest tests/e2e/test_chief_of_staff.py tests/e2e/test_board_stage_indicators.py -q`
  passed.
- Follow-up `.venv/bin/pytest tests/e2e/test_chief_of_staff.py -q` passed after removing the Chief
  nav entry.
- `.venv/bin/pytest tests/e2e/test_live_chat_state.py
  tests/e2e/test_flows_a.py::test_e22_cli_create_live_board
  tests/e2e/test_flows_a.py::test_e26_chat_panel_echo_and_offline
  tests/e2e/test_flows_b.py::test_e31_refresh_restores_state -q` passed.
- Read-only Codex diff review (`codex exec -m gpt-5.5 -s read-only`) reported `NO VIOLATIONS`.
- `.venv/bin/pytest tests/e2e/test_board_stage_indicators.py -q` passed after switching Workspace
  ordering to activity and the rail to one current-stage/status dot.
- Fresh `./verify` passed:
  - ruff ok
  - mypy ok
  - unit suite ok: 181 passed, 3 warnings
  - build check ok
  - frontend ok
  - e2e suite ok: 36 passed
  - `VERIFY: PASS`

Immediate next step: ready for owner review/commit.

Blockers: none.
