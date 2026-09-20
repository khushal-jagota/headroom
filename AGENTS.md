# AGENTS.md

There is no immutable spec. `SPEC.md` was a starting point and has been retired: design intent lives in `DESIGN.md` and the current plain-language documentation. Code and focused behavioral tests establish correctness; `./verify` is the final integration check.

**PRINCIPLES.md** holds the standing engineering and design rules; they bind unless a live owner decision overrides them.

## Stack and system map
- Python ≥ 3.12 backend in `src/planner/`, with FastAPI wiring in `src/planner/core/server.py` and SQLite schema/migrations in `src/planner/core/db.py`.
- Domain code is grouped by system: `tickets/`, `sprints/`, `days/`, and `projects/`. Contracts live in each domain's `contracts.py`; framework-free rules live in `logic/`; HTTP routes live in `api.py`.
- `panels` is the CLI entry point (`planner.cli.main:main`); `python -m planner` delegates to it. Important command groups are `serve`, `project`, `day`, `ticket`, `sprint`, `sprint item`, `worker`, and `chief`.
- Worker orchestration lives in `runtime/`. `worker_step_readiness.py` is the whole readiness decision — read-only, no writes. `worker_step_readiness_loop.py` holds both the poll thread and `start_ready_worker_step`, the per-Ticket flow: occupancy check, one guarded write that takes the worker-step claim (the only state of control a Ticket stores — no claim stamp, no run row), start or reuse the Ticket's conversation, compose the opener, send. Started and queued are both success; only a refusal releases the claim. Nothing watches a turn end. `conversation_start.py` owns resolve/start/send/reset against the conversation contract.
- Generic exact-time Ticket supply lives in `scheduled_tickets/`. Its sibling poll loop shares the server lifespan and single-machine lock, evaluates only the current local minute, and transactionally records a created, suppressed, or failed occurrence. It stops at ordinary Ticket creation and day placement; the change signal and readiness loop own the Worker handoff.
- `core/change_signal.py` is the payload-free, best-effort "something was written" signal. Every connection from `core/db.connect` announces its own commits, so no domain action carries wake or invalidation calls. The one way past it is `core/db.commit_without_change_signal`, used where the writer has established that no screen is waiting for its rows: managed skill history, and the conversation rows only an open conversation shows, which the live tail hands over directly. While the polling-lock owner's readiness loop runs it is subscribed to the signal; SQLite and the periodic timer remain canonical. The browser hears the same signal over `GET /api/changes` (`core/sse.py`).
- Conversation code lives in `conversation/`, and it is the only one: contract, core, record, live tail, backend snapshots, and one adapter per backend under `backends/`. Every screen that shows a conversation and every worker step goes through it. The backends are a closed set of exactly `hermes`, `codex`, and `claude`, stated in the contract, and one door turns untrusted text into a member of it.
- Frontend is Svelte/Vite in `web/`; FastAPI serves the built `web/dist` app at `/` and Vite chunks under `/_app/`. Shared design assets remain in `assets/`: `tokens.css` and `app.css`; the Vite-owned GFM pipeline lives in `web/src/lib/markdownPipeline.ts`.
- Panels-owned agent role skills live in `src/planner/skills/`, especially `panels` and `panels-worker`; they ship with the Python package, project-native agent roots point there, and startup exposes those same source directories to the planner Hermes home under `data/hermes-home/skills/`.
- `docs/` is the live plain-language system documentation. `orchestration/tickets/` holds historical ticket plans, dispatches, and reviews.
- `data/` is gitignored runtime state: SQLite DBs, WAL/SHM files, logs, locks, Hermes home state, smoke artifacts, and verify output.

## Worker conversation boundary
- **A database row, event, or browser update is not model context.** Only text that was actually sent into the worker's conversation is.
- To change what a worker sees, deliver the text as a real prompt into the worker's conversation. Pending worker context must be included in that prompt and acknowledged only after the send reports it got through.
- If a design depends on the worker reading human guidance, prove the guidance reaches the actual prompt. Do not treat a database row, event-log row, or UI transcript line as delivery.

## Naming and restraint
- **Name things for exactly what they are.** Descriptive beats concise — an extra word that removes ambiguity costs nothing and prevents confusion later. A name should be self-evident: the ticket's status is `ticket_status`, an employee's session id is `conversation_id`. When names are right it is obvious where a new thing belongs — a new ticket status obviously goes in the status — so you extend a clear structure instead of guessing or fitting around.
- **Everything earns its existence; understand before you extend.** When handed something to build or plan, first work out what each existing thing actually *is* and why it exists — never fit around a structure you have not understood. Add nothing without a clear, stated reason: no speculative field, status, guard, or mechanism. If you cannot say plainly why a thing exists, it should not. When a plan — yours or a reviewer's — bolts on something that wasn't asked for or doesn't clearly make sense, cut it, don't accommodate it.

## Memory
- **Panels ticket fields, recap, and artifacts** — keep the current stage, evidence, judgment calls, next step, and blockers with the ticket that owns the work.
- **docs/** — plain-language documentation of what exists and how it works, split by system (`docs/README.md` is the map; `docs/CLAUDE.md` holds the conventions). Simple sentences, no jargon — a smart non-engineer must be able to read it; if a section can't be understood without reading the code, rewrite the section. Kept **current, not frozen**: written as work completes, and corrected in the same breath when a feature changes or is removed — a doc still describing deleted machinery is a bug, not history. History is git's.

## Verification
- **A check follows a piece of work, not a file and not an edit.** Finish what you set out to do, then run the narrowest check that proves it. A broad suite re-run when nothing since could have changed it proves nothing, and at a hundred repetitions it is not a detail of the work, it is most of the wall clock.
- Reserve `./verify` for the final settled tree of a complete change or multi-ticket program. Do not run it during investigation, planning, or individual implementation chunks. During work, run only the narrow checks that prove the changed behavior. One clean final run supports the repository-wide completeness claim; save and cite its full output. Repeat only after a failure or a material subsequent change.
- Use Playwright E2E only when a material risk requires a real browser and live server together. Ticket plans or reviews must name that risk, the exercised boundary, and why frontend, unit, or integration tests cannot prove it. Keep the smallest E2E proof. Test other browser behavior in `web/tests`, and never rely on an eyeball check.
- Independent reviews may use a fresh sub-agent; the Codex CLI is not required. Use one focused review wherever a second pair of eyes materially improves confidence: completed work against its contract/design, intricate correctness logic, or a combined diff before integration. A second round is only for a concrete unresolved finding. Surface the review output and address or refute each point in writing.

## Operating model: plan and orchestrate
- You are primarily a **planner and orchestrator of sub-agents**. Your own outputs are: the plan, contract-scoped tickets, dispatches, independent reviews, serial integrations, and verification runs. Implementation substance is produced by sub-agents working tickets.
- Write code directly only when a change is too small to be worth a ticket — glue, integration repairs, one-line fixes — and note it in the owning ticket. If you catch yourself implementing a stage's substance inline, stop and cut tickets. Route, don't execute.
- A ticket is contract-scoped: it names the contract/type files it implements against, the acceptance tests it must turn green, and nothing else. Sub-agents do not invent shapes, do not modify contracts, and do not touch files outside their ticket.
- Per-ticket pipeline — each step isolated work: (1) you decompose and write the ticket; (2) a sub-agent plans the ticket's implementation; (3) an independent reviewer checks that plan against the contracts and relevant design doc; (4) a sub-agent implements to the reviewed plan; (5) an independent reviewer checks the implementation diff; (6) you integrate serially. Steps 2–5 can be collapsed for trivial tickets when the ticket records why. A multi-ticket program may explicitly reserve one full `./verify` for its final settled tree; individual tickets then use their named focused gates.
- Parallelisation is your call: decide from file overlap which tickets may share the main worktree and which need isolated git worktrees; never let two agents write the same files concurrently.
- **Anything that can run in parallel should.** Serial work is a cost, and only a real dependency justifies it. A sub-agent orchestrates too — say so, and let it fan out without asking.
- **Pick the model, then brief to it.** Opus is the default. It may do a task itself, or spawn its own agents to plan, implement and review — which of those fits is its call, not something to prescribe. Give it the task and the decisions already made, encourage it to parallelise and delegate where that makes sense, and leave the shape to it. Sonnet is for speed on a specific, well-bounded job, so brief it tightly. Fable is for large orchestrations and is rare.
- Spot-check the load-bearing code yourself even when reviews pass: the proposal resolver, the worker-step claim and its release, planning-date math, and the migration parser.
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
- At Closeout, complete integration: bring current `staging` into the Ticket branch,
  repair and verify the resulting revision, advance `staging` when it is green, push
  that exact revision to `origin/staging`, and verify the remote ref matches before
  removing the Ticket's services, local runtime state, worktree, and branch.
- Always keep a single rolling `staging` → `main` pull request open. After the
  `origin/staging` push, check whether one already exists; if not, create one. It
  updates on its own as later Closeouts advance `staging`, so there is nothing to do
  when one is already open.
- Deployment from `main` remains a later user action. It is not part of Ticket
  integration.

## Conduct
- Integrate only after the final checks pass; investigate failures rather than advancing over them. Do not use repeated broad verification as the implementation loop.
- Blocked three attempts on the same problem → record the blocker on the ticket and change approach materially, not the same idea harder.
- Do not ask questions mid-run. Delegated choices are yours; make them and record them on the ticket.

## Project notes
- Python ≥ 3.12, venv at `.venv`, deps pinned in `requirements.txt`. Run the server: `panels serve` (or `python -m planner serve`). DB and logs live under `data/` (gitignored).
- Frontend is Svelte/Vite in `web/`; FastAPI serves the built `web/dist` app at `/` and mounts Vite chunks under `/_app/`. Shared tokens, app CSS, and markdown rendering remain in `assets/`.
- Frontend reactivity is `commit → contentless change signal → invalidate everything → only mounted queries refetch`. Committing a write emits one payload-free signal, unless it is one of the few commits nothing outside its own writer reads; `GET /api/changes` streams it to the browser, which debounces and invalidates the whole TanStack query cache. Only queries a mounted screen is using refetch, and structural sharing means an unchanged answer produces no re-render — so composing survives a change landing under it. There is no client-side store of canonical state and no whole-screen refetch; the server stays the single source of truth. A new resource is just a new query in the catalogue — there is no vocabulary to keep in step.
- One canonical writer function per state transition. Agents (claim-carrying requests) write proposals only — the proposal resolver is the single door to canonical values.
