# AGENTS.md

There is no immutable spec. `SPEC.md` was a starting point and has been retired: the design intent now lives in the redesign mockups and plans under `orchestration/*-redesign/`, and the backend correctness model is the code plus its tests (surfaced by `./verify`).

**PRINCIPLES.md** holds the standing engineering and design rules; they bind unless a live owner decision overrides them.

## Stack and system map
- Python ≥ 3.12 backend in `src/planner/`, with FastAPI wiring in `src/planner/core/server.py` and SQLite schema/migrations in `src/planner/core/db.py`.
- Domain code is grouped by system: `tickets/`, `sprints/`, `days/`, and `projects/`. Contracts live in each domain's `contracts.py`; framework-free rules live in `logic/`; HTTP routes live in `api.py`.
- `panels` is the CLI entry point (`planner.cli.main:main`); `python -m planner` delegates to it. Important command groups are `serve`, `project`, `day`, `ticket`, `sprint`, `sprint item`, `worker`, and `chief`.
- Runtime work is split between `runtime/automatic_employee_step_discovery_loop.py` and `runtime/employee_step_runner.py`: the optional `AutomaticEmployeeStepDiscoveryLoop` checks today's Ticket membership against the complete Automatic Employee-step eligibility decision; the always-composed `EmployeeStepRunner` owns one Ticket step through `AcpStepGateway`.
- `runtime/automatic_employee_step_eligibility_wake.py` is only a payload-free, best-effort same-process wake. Eligibility-affecting domain actions own `commit -> wake`; SQLite and the periodic discovery timer remain canonical. The polling-lock owner uses `LoopAutomaticEmployeeStepEligibilityWake`; a process without the lock uses `NoOpAutomaticEmployeeStepEligibilityWake`.
- ACP conversation code lives in `conversation/`. One production composition owns the typed browser WebSocket, durable session bindings, backend children, human and Automatic Employee delivery, turn choices, permissions, and replay. The production backend catalog is exactly `hermes`, `codex`, and `claude`.
- Frontend is Svelte/Vite in `web/`; FastAPI serves the built `web/dist` app at `/` and Vite chunks under `/_app/`. Shared design assets remain in `assets/`: `tokens.css` and `app.css`; the Vite-owned GFM pipeline lives in `web/src/lib/markdownPipeline.ts`.
- Panels-owned agent role skills live in `src/planner/skills/`, especially `panels` and `panels-worker`; they ship with the Python package, project-native agent roots point there, and startup exposes those same source directories to the planner Hermes home under `data/hermes-home/skills/`.
- `docs/` is the live plain-language system documentation. `orchestration/*-redesign/` holds current design intent and mockups; `orchestration/tickets/` holds ticket plans, dispatches, and reviews.
- `data/` is gitignored runtime state: SQLite DBs, WAL/SHM files, logs, locks, Hermes home state, smoke artifacts, and verify output.

## Worker conversation boundary
- **A replay row, event, correctness record, or browser update is not model context.** `employee_step_runs` owns execution correctness only; ACP typed replay owns the visible conversation.
- To change what a worker sees, deliver the text as a real prompt through the worker's durable ACP session. Pending worker context must be included in that prompt and acknowledged only after ACP admits it.
- If a design depends on the worker reading human guidance, prove the guidance reaches the actual ACP prompt/session. Do not treat a database row, event-log row, or UI transcript line as delivery.

## Naming and restraint
- **Name things for exactly what they are.** Descriptive beats concise — an extra word that removes ambiguity costs nothing and prevents confusion later. A name should be self-evident: the ticket's status is `ticket_status`, an employee's session id is `employee_session_id`. When names are right it is obvious where a new thing belongs — a new ticket status obviously goes in the status — so you extend a clear structure instead of guessing or fitting around.
- **Everything earns its existence; understand before you extend.** When handed something to build or plan, first work out what each existing thing actually *is* and why it exists — never fit around a structure you have not understood. Add nothing without a clear, stated reason: no speculative field, status, guard, or mechanism. If you cannot say plainly why a thing exists, it should not. When a plan — yours or a reviewer's — bolts on something that wasn't asked for or doesn't clearly make sense, cut it, don't accommodate it.

## Memory
- **PROGRESS.md** — update every work cycle: current build stage, what just passed, current hypothesis, next step, blockers. After any context compaction, read it first — it is your memory, not the conversation.
- **decisions.md** — every delegated or judgment call, briefly justified.
- **docs/** — plain-language documentation of what exists and how it works, split by system (`docs/README.md` is the map; `docs/CLAUDE.md` holds the conventions). Simple sentences, no jargon — a smart non-engineer must be able to read it; if a section can't be understood without reading the code, rewrite the section. Kept **current, not frozen**: written as work completes, and corrected in the same breath when a feature changes or is removed — a doc still describing deleted machinery is a bug, not history. History is git's; the live build snapshot is PROGRESS.md's.

## Verification
- `./verify` is the only source of truth for completeness. Run it after changes land — not mid-work, not to re-confirm a result nothing has changed since. One clean run is the claim; show its full output and cite it. Don't re-run just to quote it.
- Browser behavior is asserted through the Playwright e2e suite inside `./verify` — never eyeballed.
- Independent reviews may use a fresh sub-agent; the Codex CLI is not required. Use one focused review wherever a second pair of eyes materially improves confidence: completed work against its contract/design, intricate correctness logic, or a combined diff before integration. A second round is only for a concrete unresolved finding. Surface the review output and address or refute each point in writing.

## Operating model: plan and orchestrate
- You are primarily a **planner and orchestrator of sub-agents**. Your own outputs are: the plan, contract-scoped tickets, dispatches, independent reviews, serial integrations, verification runs, and the memory files. Implementation substance is produced by sub-agents working tickets.
- Write code directly only when a change is too small to be worth a ticket — glue, integration repairs, one-line fixes — and note it in PROGRESS.md. If you catch yourself implementing a stage's substance inline, stop and cut tickets. Route, don't execute.
- A ticket is contract-scoped: it names the contract/type files it implements against, the acceptance tests it must turn green, and nothing else. Sub-agents do not invent shapes, do not modify contracts, and do not touch files outside their ticket.
- Per-ticket pipeline — each step isolated work: (1) you decompose and write the ticket; (2) a sub-agent plans the ticket's implementation; (3) an independent reviewer checks that plan against the contracts and relevant design doc; (4) a sub-agent implements to the reviewed plan; (5) an independent reviewer checks the implementation diff; (6) you integrate serially. Steps 2–5 can be collapsed for trivial tickets, noted in decisions.md. A multi-ticket program may explicitly reserve one full `./verify` for its final settled tree; individual tickets then use their named focused gates.
- Parallelisation is your call: decide from file overlap which tickets may share the main worktree and which need isolated git worktrees; never let two agents write the same files concurrently.
- Spot-check the load-bearing code yourself even when reviews pass: the resolution engine, dispatcher claim/reclaim, planning-date math, and the migration parser.
- A ticket is done when its named gates pass and its independent review reports no unresolved violations. When the program reserves a final canonical `./verify`, that final gate—not repeated per-ticket runs—makes the repository-wide completeness claim.

## Worktree and server flow

- Create the Ticket branch and isolated worktree from current `staging`. Do the work
  in that worktree.
- Set up the worktree's dependencies, environment inputs, generated prerequisites,
  and local runtime state, then verify that its source, dependencies, configuration,
  and state resolve to that worktree.
- For a new worktree, run:

  ```sh
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m pip install --editable .
  npm ci --prefix web
  npm ci --prefix agent_backends
  ```

  Use `git rev-parse --show-toplevel` and
  `.venv/bin/python -c 'import planner; print(planner.__file__)'` to confirm the source
  root. Run `env | rg '^PLAN_(DB_PATH|LOGS_DIR|DISPATCHER_LOCK_PATH|SERVER_CONTROL_SOCKET|HERMES_HOME)='`
  and confirm any printed path resolves to the worktree before using its runtime.
- Unchanged dependency trees may be reused when their manifests and locks match.
  Detach them before installing or changing dependencies.
- When testing, browsing, computer use, exploration, or other active work needs
  running services, survey current port use, select available ports, start the
  services, and stop them when that active work is finished. If a port is taken during
  startup, select another and retry.
- The worktree's database and other local state may remain for later use until
  Closeout.
- At Closeout, bring current `staging` into the Ticket branch, repair and verify the
  resulting revision, advance `staging` when it is green, then remove the Ticket's
  services, local runtime state, worktree, and branch.

## Conduct
- Keep `./verify` green; never advance over failing tests.
- Blocked three attempts on the same problem → log it in PROGRESS.md and change approach materially, not the same idea harder.
- Do not ask questions mid-run. Delegated choices are yours; make them and log them in decisions.md.

## Project notes
- Python ≥ 3.12, venv at `.venv`, deps pinned in `requirements.txt`. Run the server: `panels serve` (or `python -m planner serve`). DB and logs live under `data/` (gitignored).
- Frontend is Svelte/Vite in `web/`; FastAPI serves the built `web/dist` app at `/` and mounts Vite chunks under `/_app/`. Shared tokens, app CSS, and markdown rendering remain in `assets/`.
- Frontend reactivity is `events → keyed invalidation → targeted refetch` (Decision A; see spike 06 + decisions.md). The UI keeps a keyed resource cache (`ticket:<id>`, `board`, `sprint:current`, …) and refetches only the resources an event invalidates — never a whole-screen refetch, never a client-side store of canonical state (the server stays the single source of truth). Invalidation keys off the changed entity's `entity_id` prefix → its resource + the aggregates it appears in, so a new event kind about an existing entity needs no mapping change. A completeness test (required, spike 06 Phase 2) asserts every backend event kind maps to at least one resource — so when you add an event kind or a resource, a forgotten mapping fails `./verify` instead of going silently stale.
- One canonical writer function per state transition. Agents (claim-carrying requests) write proposals only — the resolution engine is the single door to canonical values.
