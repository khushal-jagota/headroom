# AGENTS.md

There is no immutable spec. `SPEC.md` was a starting point and has been retired: design intent lives in `DESIGN.md` and the current plain-language documentation. Code and focused behavioral tests establish correctness.

**PRINCIPLES.md** holds the standing engineering and design rules; they bind unless a live owner decision overrides them.

## Stack and system map
- For TypeScript server work, read [server/STYLE.md](server/STYLE.md) before changes. [server/README.md](server/README.md) describes the server's layout.
- The backend is the TypeScript server in `server/`. It runs on Node 22.18+ or 24 with Fastify and SQLite. The live VPS server and the Mac executor run it. `server/main.ts` starts it, and `server/core/start.ts` composes it. The schema starts from the frozen Python baseline in `server/core/database/python_baseline/`, which TypeScript owns. Later schema changes are TypeScript schema steps in `server/core/database/schema_steps/`, run by the installer before the server starts.
- Domain code is grouped by system under `server/`: `tickets/`, `managers/`, `ideas/`, `projects/`, and others. Contracts live in each area's `contracts.ts`; framework-free rules live in `logic/`; HTTP routes live in `routes.ts`.
- `panels` is the CLI. Its source executable is `node server/cli/main.ts`, and `server/cli/commands.ts` declares the command tree. Important command groups are `serve`, `project`, `ticket`, `manager`, `worker`, and `worker-type`. The Chief is a recipient of `send-message`, not a command group.
- Worker orchestration lives in `server/runtime/`. `worker_step_readiness.ts` is the whole readiness decision — read-only, no writes. `worker_step_readiness_loop.ts` holds both the poll loop and `start_ready_worker_step`, the per-Ticket flow: conversation busy check, one guarded write that takes the worker-step claim (the only state of control a Ticket stores — no claim stamp, no run row), start or reuse the Ticket's conversation, compose the opener, send. Started and queued are both success; only a refusal releases the claim. Nothing watches a turn end. `conversation_start.ts` owns resolve/start/send/reset against the conversation contract.
- `server/core/changes/` is the payload-free, best-effort "something was written" signal. The database writer in `server/core/database/` announces its own commits, so no domain action carries wake or invalidation calls. The one way past it is a write with `changeSignal: 'silent'`, used where the writer has established that no screen is waiting for its rows, such as the conversation rows only an open conversation shows, which the live tail hands over directly. The readiness loop is subscribed to the signal; SQLite and the periodic timer remain canonical. The browser hears the same signal over `GET /api/changes` (`server/core/routes.ts`).
- Conversation code lives in `server/conversation/`, and it is the only one: contract, core, record, live tail, and backend snapshots. Each backend is a driver package under `server/drivers/`. Every screen that shows a conversation and every worker step goes through it. The backends are a closed set of exactly `hermes`, `codex`, and `claude`, listed in `server/drivers/built_in_manifests.ts`, and one door in the conversation contract turns untrusted text into a member of it.
- Frontend is Svelte/Vite in `web/`; the server serves the built `web/dist` app at `/` and Vite chunks under `/_app/`. Shared design assets remain in `assets/`: `tokens.css` and `app.css`; the Vite-owned GFM pipeline lives in `web/src/lib/markdownPipeline.ts`.
- A little Python remains, on purpose:
  - The TypeScript Hermes driver runs two helper scripts in `src/planner/conversation/backends/`.
  - The files in `src/planner/core/migrations/versions/` are frozen data. The release now live from `main` hashes them in each staged release. No code in this tree reads them.
  - `scripts/panels_install.py` is the download the Linux machine join runs. `scripts/check_published_http2.py` serves route recovery. `ios/scripts/` holds simulator scripts.
- Skill text is a row in `managed_skills`, and the row is the authority. Panels writes `data/skills/<name>/SKILL.md` from the rows whenever a database opens, and every agent home — Hermes, Codex, Claude — links to those copies. The packaged `server/skills/seeds` tree is only what a database holding no skills is seeded from, and nothing consults it again: an edit there changes a fresh install and no running agent. Skills use `PATCH /api/skills/<name>`, atomic `POST /api/skills/apply`, `DELETE /api/skills/<name>`, and `PUT /api/skills/<name>/files` for a supporting file that lives only on disk, all behind the same owner-only door. Worker and Chief routes remain compatibility adapters. `panels skill list`, `show`, `edit`, `apply`, `delete`, and `write-file` expose this system in the CLI. Workers must hand exact live skill edits to the owner or the Chief. `panels skill check` reports a row naming a command, Stage or skill this build lacks.
- `docs/` is the live plain-language system documentation. `orchestration/tickets/` holds historical ticket plans, dispatches, and reviews.
- `data/` is gitignored runtime state: SQLite DBs, WAL/SHM files, logs, locks, Hermes home state, and smoke artifacts.

## Worker conversation boundary
- **A database row, event, or browser update is not model context.** Only text that was actually sent into the worker's conversation is.
- To change what a worker sees, deliver the text as a real prompt into the worker's conversation. There is no second input path: no row a worker is expected to pick up later.
- If a design depends on the worker reading human guidance, prove the guidance reaches the actual prompt. Do not treat a database row, event-log row, or UI transcript line as delivery.

## Naming and restraint
- **Name things for exactly what they are.** Descriptive beats concise — an extra word that removes ambiguity costs nothing and prevents confusion later. A name should be self-evident: the ticket's status is `ticket_status`, an employee's session id is `conversation_id`. When names are right it is obvious where a new thing belongs — a new ticket status obviously goes in the status — so you extend a clear structure instead of guessing or fitting around.
- **Everything earns its existence; understand before you extend.** When handed something to build or plan, first work out what each existing thing actually *is* and why it exists — never fit around a structure you have not understood. Add nothing without a clear, stated reason: no speculative field, status, guard, or mechanism. If you cannot say plainly why a thing exists, it should not. When a plan — yours or a reviewer's — bolts on something that wasn't asked for or doesn't clearly make sense, cut it, don't accommodate it.

## Memory
- **Panels ticket fields, recap, and artifacts** — keep the current stage, evidence, judgment calls, next step, and blockers with the ticket that owns the work.
- **docs/** — a top-level view of each system: what it is for, how it works, and roughly how, with just enough detail to sense-check the design. `docs/README.md` is the map, and `docs/AGENTS.md` holds the rules. Plain language that a smart non-engineer can read. When you change a system, update its page in the same change at the same depth: trim before you add, and never add a page for a feature. A doc that describes removed machinery is a bug. History is git's.

## Verification
- For iPhone work, follow [permanent screen tests and one-off reviews](ios/README.md#permanent-screen-tests-and-one-off-reviews). Keep the five admitted screen methods separate from the review scheme.
- Apply the test-admission policy in **PRINCIPLES.md** during existing plan and implementation review. Record each added test's distinct risk, existing coverage gap, chosen layer, reused setup, and any new connected case or browser host cost.
- **A check follows a piece of work, not a file and not an edit.** Finish what you set out to do, then run the narrowest check that proves it. A broad suite re-run when nothing since could have changed it proves nothing, and at a hundred repetitions it is not a detail of the work, it is most of the wall clock.
- There is no single command that checks everything. Run the checks yourself, pointed at what your work touched, and read the output. A tool that is missing or a suite that skipped says so on screen, which a wrapper reporting one number does not.
  - Server: `scripts/dev npm --prefix server run check` for types, `scripts/dev npm --prefix server run test -- <file>` for vitest, and `scripts/dev npm --prefix server run format:check`.
  - Browser app: `scripts/dev npm --prefix web run check` and `scripts/dev npm --prefix web run test -- <file>`.
- Test processes run side by side, so a test must not write outside its own temporary directory: not into the repository tree, or a fixed path or port. Give it its own copy instead.
- The browser checks are TypeScript Playwright tests in `tests/e2e/*.spec.ts`. Each test starts its own TypeScript server on a database built from the schema seed. [tests/e2e/README.md](tests/e2e/README.md) shows how to run them. Run them on hosted CI (`typescript-backend.yml`, by hand or on its schedule), not on the VPS.
- Use Playwright E2E only when a material risk requires a real browser and live server together. Ticket plans or reviews must name that risk, the exercised boundary, and why frontend, unit, or integration tests cannot prove it. Keep the smallest E2E proof. Test other browser behavior with vitest in `web/tests`, and never rely on an eyeball check.
- Independent reviews may use a fresh sub-agent; the Codex CLI is not required. Use one focused review wherever a second pair of eyes materially improves confidence: completed work against its contract/design, intricate correctness logic, or a combined diff before integration. A second round is only for a concrete unresolved finding. Surface the review output and address or refute each point in writing.

## Operating model: plan and orchestrate
- You are primarily a **planner and manager of sub-agents**. Your own outputs are: the plan, contract-scoped tickets, dispatches, independent reviews, serial integrations, and verification runs. Implementation substance is produced by sub-agents working tickets.
- Write code directly only when a change is too small to be worth a ticket — glue, integration repairs, one-line fixes — and note it in the owning ticket. If you catch yourself implementing a stage's substance inline, stop and cut tickets. Route, don't execute.
- A ticket is contract-scoped: it names the contract/type files it implements against, the acceptance tests it must turn green, and nothing else. Sub-agents do not invent shapes, do not modify contracts, and do not touch files outside their ticket.
- Per-ticket pipeline — each step isolated work: (1) you decompose and write the ticket; (2) a sub-agent plans the ticket's implementation; (3) an independent reviewer checks that plan against the contracts and relevant design doc; (4) a sub-agent implements to the reviewed plan; (5) an independent reviewer checks the implementation diff; (6) you integrate serially. Steps 2–5 can be collapsed for trivial tickets when the ticket records why. Each ticket names its own focused gates.
- Parallelisation is your call: decide from file overlap which tickets may share the main worktree and which need isolated git worktrees; never let two agents write the same files concurrently.
- **Anything that can run in parallel should.** Serial work is a cost, and only a real dependency justifies it. A sub-agent orchestrates too — say so, and let it fan out without asking.
- **Pick the model, then brief to it.** Opus is the default. It may do a task itself, or spawn its own agents to plan, implement and review — which of those fits is its call, not something to prescribe. Give it the task and the decisions already made, encourage it to parallelise and delegate where that makes sense, and leave the shape to it. Sonnet is for speed on a specific, well-bounded job, so brief it tightly. Fable is for large orchestrations and is rare.
- Spot-check the load-bearing code yourself even when reviews pass: the proposal resolver, the worker-step claim and its release, planning-date math, and the migration parser.
- A ticket is done when its named gates pass and its independent review reports no unresolved violations.

## Worktree and server flow

- Panels creates the Ticket branch and worktree before the Worker starts. Use that
  worktree through resume and restart. Do not create or remove Ticket worktrees.
- Each Ticket folder has `worktree/` for code, dependencies, and scratch, and
  `artifacts/` for documents and evidence. Use the paths in the Worker prompt.
  Panels clears the checkout when the Ticket ends or is deleted, subject to work and
  process safety. It clears the remaining Ticket folder, including artifacts, after
  seven days. Artifacts inside the checkout leave with the checkout.
  See `docs/environments.md`.
- Workers still install dependencies, commit, push, open pull requests, and perform
  approved merges. Fetch the integration target before integration. State the base
  and final checked commits in the implementation evidence.
- Set up the worktree's dependencies, environment inputs, generated prerequisites,
  and local runtime state, then verify that its source, dependencies, configuration,
  and state resolve to that worktree.
- Install only what the task needs, when it needs it, with the normal commands:

  ```sh
  npm ci --prefix server
  npm ci --prefix web
  npm install --prefix agent_backends --no-save --no-package-lock
  ```

  An older checkout can have `node_modules` folders that link into
  `~/.cache/headroom/dependencies`. Run `npm ci` there first, which replaces the links,
  before any `npm install`. Otherwise npm can write into the shared cache.
- Use the launcher for npm:

  ```sh
  scripts/dev npm --prefix web ci
  scripts/dev npm --prefix agent_backends install
  scripts/dev npm --prefix server run check
  scripts/dev npm --prefix web run check
  ```

  `scripts/dev` launches npm without a filesystem safety scan.
  Keep development dependencies and runtime state independent of the deployment.
  General prevention and race-free filesystem containment remain uncommissioned.
  Development and verification run in independent worktrees, never the installed tree.
  The artifact digest detects damage after a write. Do not add cache exclusions.
  If a write reaches the installed tree, report it and leave it in place. Do not clean it.
  Repository review or merge does not install enforcement into the deployed release.
- Run `env | rg '^PLAN_(DB_PATH|LOGS_DIR|DISPATCHER_LOCK_PATH|SERVER_CONTROL_SOCKET|HERMES_HOME)='`
  and confirm any printed path resolves to the worktree before using its runtime.
- Test databases must sit in a private temporary directory.
- When testing, browsing, computer use, exploration, or other active work needs
  running services, survey current port use, select available ports, start the
  services, and stop them when that active work is finished. If a port is taken during
  startup, select another and retry.
- Keep the worktree's database and other local state in the Ticket workspace.
  Panels owns their removal after the Ticket ends.
- At Consequences, push the Ticket branch and open an ordinary pull request into
  `staging`. Write an ordinary note about the change. `typecheck (merged tree)` is
  the one required check on `staging`. Wait for it
  to pass, merge the pull request explicitly, and verify that it merged.
  Stop the services you started. Leave workspace removal to Panels.
  If an old direct push to `origin/staging` is rejected,
  use this pull request route. If GitHub refuses the merge because of conflicts,
  bring current `staging` into the Ticket branch. Resolve the conflicts, verify the
  resulting revision, and push the branch. Wait for a fresh `typecheck (merged tree)`
  pass, merge the pull request explicitly, and verify that it merged.
- Open a `staging` → `main` pull request only when the user explicitly requests a
  release. Deployment from `main` remains a later user action.

## Conduct
- Integrate only after the final checks pass; investigate failures rather than advancing over them. Do not use repeated broad verification as the implementation loop.
- Blocked three attempts on the same problem → record the blocker on the ticket and change approach materially, not the same idea harder.
- Do not ask questions mid-run. Delegated choices are yours; make them and record them on the ticket.

## Project notes
- Node 22.18+ or 24 for `server/`. Run the server from a checkout with `node server/cli/main.ts serve`. DB and logs live under `data/` (gitignored).
- Frontend reactivity is `commit → contentless change signal → coalesced mounted-resource version probe → changed body reads`. Generic domains register authoritative source revisions. A body and its version enter the query cache together from one coherent source read. Unknown resources and failed probes retain explicit mounted-query refresh. Navigation and conversation tails keep their own scoped paths. Confirmed mutations still invalidate immediately. Inactive decoded query bodies are bounded by 64 entries and four MiB, counted once at admission, with LRU eviction and the existing age limit. Drafts and outgoing messages are outside that cache. Artifact inventory remains an explicit selected-only refresh fallback because direct filesystem writes have no DB revision.
- One canonical writer function per state transition. Agents (claim-carrying requests) write proposals only — the proposal resolver is the single door to canonical values.
