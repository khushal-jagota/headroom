# Build Prompt: Planning System v2 ("the planner")

You are building a production-quality local web application and CLI. Follow this specification exactly. Where a decision is delegated to you, the delegation is explicit and states the criteria your choice must satisfy. Nothing in this document is optional. Do not simplify, stub, or defer any specified behavior.

The design rationale behind this spec lives in `~/.hermes/planning/planning-v2-design.md` and the pattern reference is `~/.hermes/hermes-agent/docs/kanban-guide/`. Read them for understanding; this document alone defines correct.

---

## 1. What this is

A database-backed personal planning system replacing a markdown-file system. It manages sprints, sprint items, tickets, and planning days for a single user (Khushal), and lets AI agents take tickets through a shaping-and-execution pipeline under explicit per-ticket permission ceilings. It runs entirely on the local machine.

The system is: one SQLite database; one Python server process (JSON API + web UI + WebSocket event feed + embedded dispatcher + boundary scheduler); one CLI used by both agents and the human; a set of skill documents as artifacts. Agents never mutate canonical state directly — they write proposals; code resolves them.

## 2. Stack and runtime

- Python ≥ 3.12, virtualenv at `.venv`, dependencies pinned in `requirements.txt`.
- FastAPI + uvicorn for the server. SQLite (stdlib `sqlite3`) in WAL mode for storage. No ORM.
- Frontend: no-build vanilla JS + CSS served as static files by the server. No bundler, no framework, no client-side state store.
- Tests: pytest for unit; Playwright (Python, chromium, headless) for end-to-end. Lint: ruff. Typecheck: mypy (strict on `src/`).
- The server binds `127.0.0.1` only. No auth in v1. Default port 8767 (configurable).
- Layout: `src/planner/` organised by semantic domain — `core/` (shared infrastructure: db, events, config, server shell, adapters' interfaces) plus `tickets/`, `sprints/`, `days/`, `dispatch/`, `seed/`, `chat/`. Within every domain, organise by kind of work (contracts, logic, data, api) — layers within domains, never feature-slices, never one flat pile. Non-trivial things get folders.
- Every tunable (timings, TTLs, limits, ordering weights, the boundary hour) lives in the configuration module (Section 13), never inline.
- Repo root also carries: `assets/` (JS/CSS), `tests/unit/`, `tests/e2e/`, `tests/fixtures/`, `skills/`, `scripts/`, `data/` (gitignored; default DB location `data/planning.db`).
- PRINCIPLES.md at the repo root binds this build; where this spec is explicit, the spec overrides. One explicit override: the core/module registry rule is out of scope for v1 — the planner is a single product with no pluggable modules; the registry pattern applies to adapters only (spawn, boundary/replan, gateway).

## 3. Data model

All entities carry `id` (short random slug with type prefix: `sp_`, `si_`, `t_`, `day_` (day ids are `day_YYYY-MM-DD`), `idea_`), `created_at`, `updated_at` (unix seconds). All state changes append a row to an `events` table (`id` AUTOINCREMENT, `entity_id`, `kind`, `payload` JSON, `created_at`). Events are append-only: the code exposes no update or delete path for events.

### 3.1 Sprint
- `date_start`, `date_end` (inclusive, ISO dates), `name`.
- Kickoff fields (markdown text): `limiting_factor`, `primary_bet`, `supports`, `premortem`. Frozen after `kickoff_frozen_at` is set — writes rejected except `weekly_addenda` (append-only list of `{date, text}`).
- Review fields (markdown text): `outcomes`, `solo_reflection`, `joint_discussion`, `updates_to_thinking`, `carry_forward`. Frozen after `review_frozen_at` is set.
- The "current" sprint is the one whose date range contains the current planning date. Ranges must not overlap; creation rejects overlap.

### 3.2 Sprint item
- Fields: `title`, `body` (markdown), `status` ∈ {`todo`, `active`, `done`, `blocked`, `deferred_next_sprint`}, `priority` ∈ {P0, P1, P2, P3}, `deadline` (nullable ISO date), `project` ∈ {`Vylo`, `Tribe`, `Learning`, `Other`}, `current_state_note` (markdown), `sprint_id` (nullable).
- `sprint_id = NULL` means backlog/deferred work. Assigning/unassigning a sprint is a plain field update (event-logged), never a copy.
- `blocked` status carries `blocked_by`: a non-empty list of ticket ids. When every blocker reaches ticket-state `done`, the item is flagged `blockers_cleared = true` (derived, not a status change).
- Agent-permitted direct transitions: `todo ↔ active`, and setting `blocked` with blocker links. `done` and `deferred_next_sprint` require a proposal accepted by the human (Section 4.4 mechanics apply, with the item's single gating field being `status`).

### 3.3 Ticket
- Fields: `title`, `state` (Section 4.1), `priority` ∈ {P0..P3} (default P3), `deadline` (nullable date), `project` (same enum, nullable when parented — derived from parent's item), `sprint_item_id` (nullable), `sprint_id` (nullable; writable only when `sprint_item_id` is NULL — writes rejected otherwise; when parented it is derived from the parent item), `recap` (markdown, writable only when state is past `needs_success`; writes before that are rejected with a structured error), `ceiling` (a ticket state; Section 4.3), `at_cap` ∈ {`stop`, `suggest`}, `auto_blocked` (bool, Section 7.5), `chat_session_key` (nullable string), `fields` (Section 4.2).
- Title length ≤ 200 chars, enforced on every write path.

### 3.4 Day
- `id = day_YYYY-MM-DD` where the date is the **planning date**: local time shifted by the boundary hour (Section 6.1). Day rows are created on first read or write for a date; reading a nonexistent day materializes it empty. There is no "missing day" state.
- Fields: `brief` (markdown), `notes` (markdown), `plan` (Section 6.3 tree, JSON), plus an ordered ticket list: `day_tickets(day_id, ticket_id, position)` with contiguous positions from 0. Removing a ticket from a day deletes the association only (this is "deferring"; ticket state is untouched) and logs a `day_ticket_removed` event.

### 3.5 Idea
- `title`, `body` (markdown), `project` (nullable, same enum). No priority, no status, no other fields.

### 3.6 Links
- One table: `links(from_id, to_id, kind)` with kind ∈ {`belongs_to` (ticket→sprint item), `parent_child` (ticket→ticket), `blocks` (ticket→ticket or ticket→sprint item), `relates` (any→any)}.
- A ticket has at most one `belongs_to` link (enforced). Self-links rejected. `blocks` and `parent_child` cycles rejected at write time (transitive check).
- A ticket is **blocked** iff it is the target of a `blocks` link whose source ticket is not `done` or `dropped`. Blocked tickets are ineligible for dispatch (Section 7.2).

## 4. Ticket lifecycle and the write model

### 4.1 States

`needs_success → needs_approach → needs_plan → in_progress → needs_review → done`, plus `dropped` (terminal, reachable from any non-`done` state, human-only action).

There is no stored "ready" or "satisfied" state. Derived views (Section 4.5) express them.

### 4.2 Fields as structured objects

`tickets.fields` is a JSON object with exactly four keys: `success`, `approach`, `plan`, `result`. Each is `{value: string|null, proposal: {body, proposed_by, created_at}|null, notes: string|null}`. `value` is canonical and only ever written by the resolution engine (Section 4.4). `notes` is free-guidance text ("when doing the approach, consider X"), writable directly by human or agent at any time (event-logged, no state effect).

Each pre-terminal state has one **gating field**:

| state | gating field | accepted proposal advances to |
|---|---|---|
| needs_success | success | needs_approach |
| needs_approach | approach | needs_plan |
| needs_plan | plan | in_progress |
| in_progress | result | needs_review (or done, Section 4.4) |
| needs_review | — (human approval of `result.value`) | done |

### 4.3 Ceiling and at-cap

- `ceiling` is the highest state an agent-caused advancement may move the ticket **into**. Human actions ignore the ceiling entirely (the human may jump a ticket to any state, including straight to `done`; skipped gating fields simply keep `value = null`).
- `at_cap = stop`: agent proposals on any field other than the current state's gating field are rejected with a structured error. `at_cap = suggest`: agents may file proposals on later fields; those proposals never advance state until the ticket's current state reaches them.
- Defaults at creation: `ceiling = needs_success`, `at_cap = stop` [RULING R2 default].

### 4.4 The resolution engine

The only write path for `fields.*.value` and for agent-caused state changes. Semantics, exactly:

1. An agent files a proposal on a field (CLI/API). At most one pending proposal per field: a new proposal replaces the pending one (replacement logged as `proposal_superseded`).
2. If the field is the current state's gating field AND the advance-target state ≤ `ceiling`: the proposal auto-accepts — `value` ← proposal body, proposal cleared, state advances one step, events `proposal_accepted` + `state_changed` logged with `resolved_by: "auto"`.
3. Otherwise the proposal stays pending. Pending proposals on the current gating field put the ticket in the human approval queue.
4. Human resolution of a pending proposal: **accept** (value ← body), **edit** (value ← human-edited text; logged as accepted with `edited: true`), or leave it and discuss in chat. There is no reject action; a proposal can be superseded or the ticket dropped.
5. Special case `in_progress`: an accepted `result` advances to `needs_review` unless `ceiling = done`, in which case it advances directly to `done`. Human approval in `needs_review` (a dedicated approve action, not a field proposal) sets `done`.
6. Every transition writes exactly one `state_changed` event `{from, to, cause}`. One canonical function per transition edge in the code; no second writer.

### 4.5 Derived views (must exist as queryable API endpoints, not stored columns)

- **Approval queue**: tickets with a pending proposal on their current gating field (plus sprint items with pending status proposals, plus `needs_review` tickets). Ordered oldest-pending first.
- **Pickup queue** (dispatcher eligibility): Section 7.2.
- **Overdue**: tickets/items with `deadline` < current planning date and not `done`/`dropped`.

## 5. Sprints — behavior rules

- Creating a sprint with kickoff fields sets nothing frozen; an explicit `freeze-kickoff` action sets `kickoff_frozen_at`. Same pattern for review. Frozen-field writes are rejected with a structured error.
- Sprint view data: items grouped by status, each with its ticket rollup (counts by ticket state), plus a **loose tickets** section: tickets whose `sprint_id` = this sprint and `sprint_item_id` IS NULL.

## 6. Days, the boundary, and the day plan

### 6.1 Planning date

Boundary hour: **05:00 local** [RULING R1 default]. Planning date = calendar date of (now − 5 hours). At 04:59 local on July 5 the planning date is July 4; at 05:00 it is July 5. Every "today" in the system uses this rule. In test mode (Section 13) the clock is injectable.

### 6.2 The boundary job

Runs once per planning date, at the first scheduler tick at/after the boundary hour (and never twice for the same date; a `boundary_runs` table records completions). It has a deterministic pass (pure code) and a judgment pass (agent adapter):

Deterministic pass:
1. Materialize the new day row.
2. Compute carryover candidates: yesterday's day-tickets whose ticket state is not `done`/`dropped`.
3. Compute overdue list and the approval-queue digest.
4. Close out yesterday: log a `day_closed` event with a summary payload {counts of done/not-done day tickets}.

Judgment pass (via the **boundary agent adapter**, one call): given the deterministic outputs, it returns `{brief_markdown, plan_tree}`. The result is stored as the day's `brief` and a **proposed** plan (Section 6.3). If the adapter fails or times out (60s), the day still exists with an empty brief and no plan; the failure is event-logged. v1 evidence is DB-internal only — no repo or session scanning [RULING R4 default].

If the human has already planned the day explicitly (any day-ticket or accepted plan exists) before the boundary job's judgment pass, the judgment pass is skipped entirely.

### 6.3 The day-plan tree

`day.plan` is JSON: `{root: {focus: string, status}, children: [{ticket_id, note, status, position}]}` — one level of children in v1. Node `status` ∈ {`proposed`, `accepted`, `invalidated`}.

- Accepting a node sets it `accepted`. **Accept-all** accepts root and all children; accepting the root alone does not cascade. When a child with a `ticket_id` is accepted, the ticket is added to the day's ticket list (at the end) if not already present.
- Invalidating the root sets root and every child `invalidated` and triggers one replan-adapter call that returns a complete new tree (replacing the old; old tree preserved in the event payload). Invalidating a child replans only that node: the adapter returns a replacement child. Replans serialize; if an invalidation arrives while a replan is in flight, the in-flight result is discarded and one new replan runs (latest wins) [RULING R5 default].
- **Reject-all**: clears `plan` to null (event-logged); the human plans manually.

## 7. The dispatcher and runs

### 7.1 Tick

Every 60s (configurable), inside the server process. Order within a tick: (1) reclaim expired claims and detect dead worker processes; (2) recompute eligibility; (3) spawn up to the concurrency cap. A machine-wide advisory file lock (`data/dispatcher.lock`) guarantees a single dispatcher; a `dispatch_enabled` config flag is re-read every tick and fails safe to `false` on config read error.

### 7.2 Eligibility and ordering

A ticket is eligible iff ALL of: not `done`/`dropped`/`needs_review`; not blocked (3.6); not `auto_blocked`; no active claim; and an agent can do work on it — meaning (a) the current gating field has no pending proposal and the advance-target ≤ `ceiling`, or (b) `at_cap = suggest` and some later field lacks both value and pending proposal, or (c) state is `in_progress` and `result` has no pending proposal and `needs_review` ≤ `ceiling`. Ordering: priority (P0 first), then deadline ascending with NULLs last, then `created_at` ascending.

### 7.3 Claims and runs

- Claim = CAS: `UPDATE tickets SET claim_lock=?, claim_expires=? WHERE id=? AND claim_lock IS NULL`; rowcount 0 = lost. TTL 15 minutes. Every claim inserts a `runs` row (`ticket_id`, `status` ∈ {running, done, blocked, crashed, timed_out, reclaimed}, `started_at`, `ended_at`, `summary`, `error`, `pid`).
- Heartbeat (CLI verb) extends `claim_expires` by one TTL. Expired claim or dead PID → run closed `reclaimed`, lock cleared.
- Max concurrent runs: 2 [RULING R7 default]. Per-run max runtime: 30 minutes → SIGTERM, run `timed_out`.

### 7.4 Spawning

Through a **spawn adapter** (subprocess boundary, mockable): `hermes -p <profile> --skills <skill> chat -q "work planning ticket <id>"`, detached session, per-run log file under `data/logs/`, env: `PLAN_SERVER_URL`, `PLAN_TICKET_ID`, `PLAN_RUN_ID`, `PLAN_CLAIM`. Profile and skill names come from config [RULING R3 default: profile `default`, skill `planning-worker`]. Tests never spawn real processes.

### 7.5 Circuit breaker

A run ending `crashed`, `timed_out`, or `spawn_failed` increments `tickets.consecutive_failures`; a run ending `done` resets it to 0. Reaching 2 sets `auto_blocked = true` (sticky; event `auto_blocked`). The dispatcher never picks up an auto-blocked ticket; a human clear-action resets both flag and counter.

### 7.6 Server-side write validation

Every agent write (proposal, recap, heartbeat, run close) must carry `PLAN_RUN_ID` + `PLAN_CLAIM` matching the ticket's active claim, except proposal/recap writes from non-dispatched contexts (human CLI without claim env) which are permitted as `proposed_by: "khushal"`. A write with a stale/foreign claim is rejected with a structured error naming the mismatch.

## 8. The CLI (`plan`)

A single entry point (`plan`, installed via `pip install -e .` console script) speaking HTTP to the server. Global behavior: `--json` on every verb (machine output; stable exit codes: 0 success, 1 validation/domain error, 2 connection error); long text via `--body-file <path>` or `-` for stdin — body text is never passed as an inline argument; env defaults `PLAN_SERVER_URL`, `PLAN_TICKET_ID` (verbs taking a ticket id use it when omitted), `PLAN_RUN_ID`, `PLAN_CLAIM`.

Verbs (exact):
- `plan serve` — run the server (foreground).
- `plan seed --source <dir>` — Section 12.
- `plan ticket create|show|list|drop|set` (`set` for priority/deadline/ceiling/at-cap/day-assignment/sprint-assignment; ceiling/at-cap changes are human-only — rejected when claim env present).
- `plan propose <field>` (body via stdin/file) · `plan recap` (same input rules) · `plan note <field>`.
- `plan accept <ticket> <field> [--edit-file <path>]` · `plan approve <ticket>` (needs_review→done) · `plan unblock <ticket>` (clears auto_block).
- `plan item create|show|list|set|propose-status` · `plan sprint create|show|freeze-kickoff|freeze-review|set` · `plan idea create|list`.
- `plan day show [date]|plan-accept-all|plan-reject-all|invalidate <node>|add-ticket|remove-ticket`.
- `plan link add|rm <from> <to> --kind <kind>` · `plan run heartbeat|close --outcome <o> --summary -`.
- `plan queue approvals|pickup|overdue` — the derived views.

## 9. HTTP API and events

- REST JSON under `/api/`: CRUD + actions exactly mirroring Section 8 verbs, plus `GET /api/board`, `GET /api/day/<date>`, `GET /api/sprint/current`, `GET /api/queues`. Every mutating endpoint routes through the same canonical functions as the CLI (one writer per edge).
- `WS /api/events?since=<id>`: tails the `events` table (poll interval 300ms server-side), pushing `{events:[...], cursor}` batches. Used by the UI as an **invalidation signal only** — the UI refetches JSON endpoints (debounced 250ms); it never reconciles event payloads into local state.
- Test mode (Section 13): `POST /api/test/tick-boundary` and `POST /api/test/tick-dispatcher` run one tick synchronously; available only when test mode is on, else 404.

## 10. The UI

Five screens, served at `/`. Simplicity is the ruling aesthetic: fewer things done cleanly. Every screen renders from server JSON; refresh at any moment restores identical state.

Design system (per PRINCIPLES.md, binding):
- One token file, `assets/tokens.css`, owns everything themable: surfaces, text tokens, accent (bright/surface/text), radius scale, motion durations (fast/base/slow), spacing scale, border widths. The app's entire personality must be tunable by editing this one file. No new token categories without evidence of need.
- Type: strict scale, five sizes maximum. Motion: entrances ease out, nothing bounces, all durations from motion tokens. Add nothing to a screen unless it makes the user feel something or a smart person genuinely needs it to understand the screen; no explanatory text for the obvious.
- Before writing any component, record the component inventory for all five screens in decisions.md; build few, reuse hard, no near-duplicates.

1. **Day (home)**: the brief (rendered markdown), the day-plan tree with per-node Accept / Invalidate and top-level Accept-all / Reject-all, the day's ticket list (ordered), and the approval queue inline (each entry: ticket title, field, proposal body rendered, Accept / Edit buttons — Edit opens a textarea prefilled with the proposal).
2. **Board**: one column per ticket state (`dropped` hidden), cards show title, priority, deadline, project, pending-proposal marker, running-claim marker. No drag-and-drop in v1: card click opens Ticket.
3. **Ticket**: all four fields as sections (value rendered as markdown; pending proposal shown side-by-side with Accept/Edit; notes editable inline), recap, state/ceiling/at-cap controls, links, day/sprint assignment, run history, event log, and the chat panel (Section 11).
4. **Sprint**: kickoff fields, items grouped by status (with ticket rollup counts and blockers-cleared flags), loose tickets, review fields (frozen states shown as locked).
5. **Backlog & Ideas**: itemless sprint items list (by priority) and ideas list, each with create forms.

## 11. Chat

Each ticket and each day has at most one chat session. The chat panel connects through a **gateway adapter** wrapping the Hermes gateway (`tui_gateway.ws` import, same pattern as the old viewer). If the gateway is unreachable the panel renders "gateway offline" and the rest of the UI is unaffected. The entity's `chat_session_key` persists the session id. In tests the adapter is a fake that echoes. Live gateway behavior is a post-run human item, not an acceptance test.

## 12. Seed and migration

Migration from the markdown system is an in-run deliverable, not an afterthought. A frozen snapshot of the real planning data (taken 2026-07-04) is committed at `migration/source-snapshot/`; the build must migrate it successfully. The live directory is still never read at build/test time — the snapshot is the real-data target; final live cutover at switch time is the human's moment (§18.4).

`plan seed --source <dir>` reads a markdown planning directory with the current system's shapes and imports: `sprints/current/sprint-kickoff.md` → sprint kickoff fields; `sprint-tracking.md` items → sprint items (statuses: Todo→todo, In Progress→active, Done→done, Blocked→blocked, Deferred→deferred_next_sprint; `Priority:`/`Urgency:`/`Project:` fields mapped; Mode dropped); `sprint-review.md` → review fields; the latest `daily/YYYY-MM-DD/workspace.md` tickets → tickets (Readiness mapping: Concepts→needs_success, Needs Shaping→needs_approach, Ready→needs_plan, In Progress→in_progress; `Ticket ID:` preserved as an alias field; `Chat ID:` → `chat_session_key`; `Priority:` mapped; body/success/approach text into the matching fields' values) linked to sprint items by title match when unambiguous, else standalone with sprint assignment; `deferred.md` → sprint items with `sprint_id = NULL` (project sections and P-labels mapped); `ideas.md` → ideas. `plan seed --demo` creates a deterministic demo dataset for dogfooding and manual testing: 8 tickets spanning every state with varied priorities, deadlines, and ceilings; one blocking link; one parent sprint item with children; one planned day. It requires an empty database and errors otherwise.

Archives and historical daily folders are not imported — only the latest day's workspace feeds tickets [RULING R6 default]. Seeding is idempotent by alias/title: re-running against the same source creates no duplicates. Every seed run emits a migration report (printed, and with `--json` structured): counts imported per entity kind, plus an explicit list of skipped/unparseable sections — silent drops are forbidden. Unit-tested against synthetic fixtures in `tests/fixtures/`; additionally proven end-to-end against `migration/source-snapshot/` (item 34), whose known ground truth is: 1 sprint (2026-07-01 → 2026-07-12); 12 sprint items (6 todo, 5 active, 1 done — the done item titled "Ship waitlist mechanics."); 4 tickets from the 2026-07-03 workspace (mic-publish and app-typography at needs_plan; landing-gate-1 and durable-personas at in_progress; `Chat ID: 20260702_114500_0ec57a` preserved on landing-gate-1); 9 deferred items (NULL sprint, projects mapped from the Vylo/Tribe/Learning/Other headings); 20 ideas. The live directory is never read in tests.

## 13. Configuration and test mode

- `config.yaml` at repo root (checked in with defaults) + env overrides: `PLAN_DB_PATH` (default `data/planning.db`), `PLAN_PORT` (8767), `PLAN_BOUNDARY_HOUR` (5), `PLAN_TICK_SECONDS` (60), `PLAN_CLAIM_TTL_SECONDS` (900), `PLAN_MAX_RUNS` (2), `PLAN_FAILURE_LIMIT` (2), `PLAN_DISPATCH_ENABLED` (true), `PLAN_HERMES_BIN`, `PLAN_HERMES_PROFILE`, `PLAN_WORKER_SKILL`.
- Test mode: `PLAN_TEST_MODE=1` enables `PLAN_FAKE_NOW` (ISO datetime honored everywhere the clock is read) and the `/api/test/*` endpoints. When `PLAN_TEST_MODE` is unset, `PLAN_FAKE_NOW` is ignored and test endpoints 404.

## 14. Structural rules

- **One canonical writer per transition edge.** Every state/status change goes through exactly one function; API and CLI both call it.
- **Proposals-only for agents.** No code path lets a request carrying claim env write `fields.*.value` or change state except via the resolution engine.
- **Adapters at every external boundary**: spawn (subprocess), boundary/replan agent, gateway chat. Each is a small interface with a real and a fake implementation; tests use fakes; nothing in tests shells out to `hermes` or opens network connections beyond localhost Playwright↔server.
- **Append-only events**; derived views computed on read, never stored.
- **Contracts first**: each domain has a contracts file (types, enums, shapes) generated at stage 1; all implementation imports its types from contracts and never redeclares a shape locally; the frontend consumes exactly the JSON shapes the contracts document. A shape change means changing the contract file and letting type errors drive the fixes.
- **Pure logic dependency-free**: state machine, resolution, eligibility, ordering, seed parsing, and planning-date math live in each domain's logic layer as plain functions importing nothing but stdlib (and domain contracts); FastAPI/DB code imports them, never the reverse. The acid test: if a rule can't be unit-tested with no mocks, it's in the wrong place.
- Never touch `~/.hermes/planning/` (the live markdown system), `~/.hermes/hermes-agent/`, or any path outside this repository at build/test time.

## 15. Non-goals (v1)

No push notifications, no iOS client, no auth/multi-user, no weekly-check cron, no repo/session evidence adapters, no Necessary-Calls entity, no Mode field, no drag-and-drop, no markdown export mirror, no archive import.

## 16. Rulings in force

Defaults the owner may override before the run; each is tagged where it binds: **R1** boundary hour 05:00. **R2** new-ticket grant `ceiling=needs_success, at_cap=stop`. **R3** spawn profile `default`, worker skill `planning-worker`. **R4** boundary evidence DB-internal only. **R5** replan serialization, latest-wins. **R6** seed excludes archives. **R7** max concurrent runs 2.

## 17. Skills artifacts

Write four skill documents under `skills/` (not installed anywhere by the build): `planning-worker.md` (how a dispatched agent works a ticket via the CLI: orient with `plan ticket show --json`, propose via stdin, recap discipline, heartbeats, never asking questions), `planning-boundary.md` (the judgment pass contract), `planner-main.md` (the day/system chat agent: operating the queues and day plan via CLI), `planning-executor.md` (working an in_progress ticket you own). Each ≤ 150 lines, written for the agent that will load it. DOCS.md summarizes their roles.

---

## 18. Delivery expectations and acceptance checklist

**Build in this order**: (1) contracts skeleton — schema DDL, enums, typed models, adapter interfaces, config loading, CLI/API surface stubs; (2) **the verify instrument** (18.2), runnable with every checklist item FAIL; (3) pure-logic modules + unit tests 1–21 green; (4) server wiring — API, WS, resolution engine over DB, dispatcher, boundary scheduler, CLI against the API; (5) UI views; (6) Playwright e2e suite, items 22–34 green; (7) the fake-ticket dogfood pass (Section 18.5): Level A green (item 35), Levels B and C executed with evidence recorded in DOGFOOD.md. Do not start a later stage while an earlier stage's tests fail.

Where this document delegates a choice, make it, implement it fully, and record it in decisions.md. If a genuine contradiction emerges, state it in PROGRESS.md and propose a resolution consistent with Section 14's rules — do not silently pick.

### 18.1 Required artifacts

- **PROGRESS.md** — updated every work cycle: current stage, what just passed, current hypothesis, next step, blockers. First read after any context compaction. Three failed attempts on one problem → log it and change approach materially.
- **decisions.md** — every delegated choice, briefly justified.
- **DOCS.md** — plain-language documentation written progressively as each stage completes, never retrofitted. A smart non-engineer must be able to read it: what the planner is, the entities, how proposals and ceilings work, what the dispatcher does, how a day flows, what each screen shows, what the CLI verbs do, what the skills are for. If a section needs the code to be understood, rewrite the section.
- **DOGFOOD.md** — the record of the Section 18.5 fake-ticket dogfood: per level, what was run, the exact commands, the evidence (event-log excerpts, run rows, session/log file paths under `data/logs/`), and an honest outcome. Narrative without corresponding logs is not evidence.

### 18.2 The verify instrument

- One command: `./verify` (executable script at repo root). Runs in order: ruff, mypy, unit suite, build check (`python -m compileall src/` + `node --check` on every file in `assets/`), then the Playwright e2e suite headless. Prints a per-item scoreboard for every acceptance test below (item number, name, PASS/FAIL) and ends with exactly `VERIFY: N/35 PASS`.
- The instrument fails with an explicit message if any test file contains `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, a commented-out test, or an empty test body — the scan itself is unit-tested (item 21).
- Built at stage 2, before implementation; all items report FAIL until their stage lands. The scoreboard is the single source of truth for completeness.
- Items 22–35 are end-to-end; item 34 (snapshot migration) runs against `migration/source-snapshot/`, and item 35 (dogfood Level A) is the scripted CLI walkthrough — both part of the suite, not manual steps. Levels B and C of the dogfood (Section 18.5) are deliberately OUTSIDE `./verify`: they use live agents and belong to stage 7's evidence, not the deterministic gate.

### 18.3 Acceptance tests

**Fences:**
- Each item maps 1:1 to a named test whose name includes the item number (`test_a01_...`, `test_e22_...`).
- Assertions use the specific values in this document — exact states, exact orderings, exact error shapes — not weakened approximations.
- After first written, any change to a test requires a logged justification in decisions.md.
- A claim of passing is valid only if `./verify` ran fresh in the same turn with full output shown.

External boundaries (hermes spawn, boundary/replan agent, chat gateway) are faked in all tests. E2E uses a real server + temp SQLite DB + `PLAN_TEST_MODE=1`, driving ticks via the test endpoints and time via `PLAN_FAKE_NOW`. Multi-surface behavior uses two browser contexts.

**Unit (pytest, pure logic + engine over a temp DB):**
1. Planning-date math: 2026-07-05T04:59 local → planning date 2026-07-04; T05:00 → 2026-07-05; boundary hour honored from config.
2. Gating chain: accepting success/approach/plan/result proposals advances exactly one state each, in order, with one `state_changed` event per step.
3. Ceiling auto-accept: ceiling `needs_plan`, proposals filed on success then approach auto-accept and advance; the plan proposal stays pending (state `needs_plan`, ticket in approval queue).
4. At-cap stop vs suggest: with `stop`, an agent proposal on a non-gating field returns the structured error; with `suggest`, it files as pending and does not advance state.
5. One pending proposal per field: a second proposal supersedes the first; `proposal_superseded` event carries the replaced body.
6. Edit-accept: accepting with edited text stores the edited text exactly as `value`, flags `edited: true` in the event.
7. Result routing: accepted result with ceiling `needs_review` → state `needs_review`; with ceiling `done` → `done`; approve action in `needs_review` → `done`.
8. Recap: write at `needs_success` rejected with structured error; write at `needs_approach` succeeds, overwrites prior recap, logs event, changes no state.
9. Blocking: ticket blocked by an open ticket is dispatch-ineligible; blocker → `done` makes it eligible; `blocks` cycle creation rejected.
10. Sprint-item permissions: agent `todo→active` succeeds; agent direct `active→done` write rejected; `done` via proposal + accept succeeds; `blocked_by` list stored and `blockers_cleared` computed when all blockers done.
11. Dispatch ordering: fixture of five eligible tickets orders P0 before P1; equal priority by earlier deadline; NULL deadline last; then `created_at`.
12. Day-ticket removal: deletes association only, position list re-packs contiguously, ticket state unchanged, `day_ticket_removed` logged.
13. Sprint assignment rules: standalone ticket accepts `sprint_id`; parented ticket's `sprint_id` write rejected; parent's sprint derived.
14. Claim CAS: two concurrent claim attempts on one ticket → exactly one wins, one `runs` row created.
15. TTL/reclaim: expired claim reclaimed (run `reclaimed`, lock cleared, eligible again); heartbeat extends expiry by one TTL.
16. Circuit breaker: two consecutive `crashed` runs → `auto_blocked`, ineligible; unblock action clears flag and counter; a `done` run resets the counter.
17. Day-plan tree: root invalidation marks root + all children invalidated and requests one replan; child invalidation replaces only that child; accept-all accepts every node and adds child tickets to the day list exactly once.
18. Boundary job (fake clock): first tick ≥ 05:00 creates the day, computes carryover (yesterday's non-done day tickets), overdue list, logs `day_closed`; second tick same date does nothing; explicit prior planning skips the judgment pass.
19. Seed fixtures: importing `tests/fixtures/planning-md/` yields the exact expected entity counts and spot-checked values (item statuses, ticket states per the Readiness mapping, preserved Chat ID, deferred items with NULL sprint, idea with project); re-running seeds zero duplicates.
20. Freeze rules: kickoff write after freeze rejected; `weekly_addenda` append still allowed; review same pattern. Sprint overlap rejected.
21. Instrument integrity: the skip-scan detects each forbidden pattern (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, empty body) in a synthetic file set and passes a clean set; `PLAN_FAKE_NOW` is ignored when `PLAN_TEST_MODE` is unset.

**End-to-end (Playwright, chromium, two browser contexts where stated):**
22. CLI create → live board: `plan ticket create` via subprocess with env pointing at the test server; the ticket appears on the Board in `needs_success` in a second context without reload.
23. Env-pinned propose: `plan propose success` with `PLAN_TICKET_ID` set and a multi-line markdown body on stdin; Ticket screen shows the proposal rendered, intact.
24. Accept in UI: the pending proposal shows on Day's approval queue; Accept advances the ticket; both contexts update without reload.
25. Edit-accept in UI: Edit opens prefilled textarea; submitting altered text stores the altered text as the field value shown on Ticket.
26. Chat panel: opens on Ticket with the fake gateway (echo); a sent message renders a reply; `chat_session_key` persisted (asserted via API); gateway-offline fake renders the offline notice.
27. Auto-accept chain e2e: ticket with ceiling `needs_plan`: two CLI proposals advance it to `needs_plan` with the plan proposal pending in the approval queue.
28. Day view: with a fake boundary adapter, driving `POST /api/test/tick-boundary` at fake-now 05:01 produces the brief and a proposed tree; Accept-all adds the child tickets to the day list.
29. Invalidation: invalidating the root calls the fake replan adapter and renders the replacement tree; invalidating one child replaces only that child (other nodes keep status).
30. Dispatcher e2e: eligible ticket + fake spawn adapter; `POST /api/test/tick-dispatcher` claims it (run visible on Ticket); the fake worker's CLI calls (propose result with claim env) park it in `needs_review`; it appears in the approval queue; approve → `done`.
31. Refresh restores state: on Day, Board, and Ticket mid-flow (pending proposal, running claim), a reload renders identical content.
32. Sprint view live: `plan item set --status active` via CLI reflects on the Sprint screen in both contexts without reload; loose ticket appears in the loose section.
33. Seed e2e: `plan seed --source tests/fixtures/planning-md/` against the test server, then Board/Sprint/Backlog show the fixture's expected titles and counts.
34. Snapshot migration e2e: `plan seed --source migration/source-snapshot/` imports the frozen real data with exactly the ground truth stated in Section 12 (1 sprint with those dates; 12 items split 6/5/1 with "Ship waitlist mechanics." done; the 4 named tickets in their mapped states with the Chat ID preserved; 9 deferred items; 20 ideas); the migration report lists zero silently-skipped sections (anything unparsed is enumerated in it); a second run imports zero duplicates.
35. Dogfood Level A: `scripts/dogfood_cli.py` drives the full user+agent workflow through the real CLI against a test server seeded with `plan seed --demo`: create a ticket; propose and accept through every gate including one edit-accept and one superseded proposal; write and overwrite the recap; assign to the day and remove; create a blocking link and confirm dispatch-ineligibility, then complete the blocker and confirm eligibility; claim via the test dispatcher tick; propose result with claim env; approve from needs_review to done. The script asserts each step's resulting state via `--json` output and exits non-zero on the first mismatch.

### 18.5 Fake-ticket dogfood (stage 7)

The application must be proven with realistic fake tickets at three escalating levels of automation. This is the one sanctioned exception to the fakes-only rule: Levels B and C use the real local Hermes runtime — never inside `./verify`, always recorded in DOGFOOD.md with log evidence.

- **Level A — the CLI as a user and agent would drive it.** The scripted walkthrough of item 35. Deterministic, gating, part of `./verify`.
- **Level B — a Hermes agent works a fake ticket.** Spawn one real session: `hermes chat -q "Read <repo>/skills/planning-worker.md, then work planning ticket <id> using the plan CLI"` with the agent env vars pinned (server URL + ticket id) against a running server with a demo-seeded ticket shaped for it (ceiling `needs_plan`, `at_cap = suggest`). Pass evidence, mechanical: at least one proposal and one recap write recorded on that ticket by the session (asserted from the event log), session output captured under `data/logs/` and referenced in DOGFOOD.md. The repo's skill artifacts are exercised via the read-this-file prompt because they are not installed into `~/.hermes/skills/` during the run.
- **Level C — a dispatched ticket agent end to end.** Enable the real spawn adapter (config), one eligible demo ticket, real dispatcher tick: claim → run row → the spawned agent writes at least one proposal carrying the claim env → run closes with a terminal outcome and the ticket lands where its ceiling dictates. Evidence: the run row, the event sequence, and the per-run log path, all in DOGFOOD.md.

Levels B and C depend on live agent behavior: apply the three-attempt rule per level (materially different approach each time — prompt shape, ticket shaping, ceiling), and record every attempt honestly in DOGFOOD.md. Completion requires both levels' mechanical evidence. If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run.

### 18.4 Post-run human pass (not tests)

Live Hermes gateway chat; live dispatcher spawning a real agent; the final cutover seed against the live `~/.hermes/planning/` at switch time (the in-run migration proves the machinery on the frozen snapshot; the live directory will have drifted since 2026-07-04 and its import is reviewed by the human); installing skills into `~/.hermes/skills/`; launchd supervision; UI look-and-feel judgment.
