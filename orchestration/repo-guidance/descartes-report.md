# Descartes repo guidance report

No edits made. Descartes inspected the requested guidance files, project tree, and main implementation areas.

## Repo map

- Backend: `src/planner/` Python 3.12 package, FastAPI app in `src/planner/core/server.py`, SQLite schema/migrations in `src/planner/core/db.py`.
- Domains: `tickets/`, `sprints/`, `days/`, `projects/`, `chat/`; each generally has `contracts.py`, `data.py`, `api.py`, and sometimes `logic/`.
- CLI: console script is `panels = planner.cli.main:main`; `python -m planner` delegates to it. Main command groups: `serve`, `project`, `day`, `ticket`, `sprint`, `sprint item`, `worker`.
- Runtime: `runtime/system_a.py` polls runnable tickets on today's board; `runtime/system_b.py` runs one ticket step through the shared gateway.
- Gateway/chat: `minds/gateway.py` owns JSON-RPC child transport; `minds/shared_gateway.py` owns shared Hermes session access; `chat/` exposes human chat, streaming, slash command catalog, and history.
- Frontend: Svelte/Vite app in `web/src/`; built output in `web/dist/`; FastAPI serves `/` from `web/dist/index.html` and Vite chunks under `/_app/`.
- Shared frontend assets: `assets/tokens.css`, `assets/app.css`, `assets/markdown.js`.
- Skills/roles: local skill files live under `skills/`; key runtime roles are `skills/panels/SKILL.md`, `skills/panels-worker/SKILL.md`, and `skills/panels/panels-ticket-work/SKILL.md`. Startup provisions skills into Hermes home via `planner.minds.config`.
- Tests/verify: `./verify` runs ruff, mypy, unit tests, asset syntax checks, frontend check/build/test, and e2e tests.
- Docs/orchestration: live plain-language docs under `docs/`; design/planning intent under `orchestration/*-redesign/`; ticket work records under `orchestration/tickets/`.
- Data boundary: `data/` is gitignored and holds SQLite DBs, WAL/SHM files, logs, locks, Hermes home state, verify output, and smoke/migration artifacts.

## What `AGENTS.md` should include or clarify

- Add a repo map like the above so agents orient without rediscovering structure.
- Clarify `panels` is the command, not `plan`.
- Clarify frontend is Svelte/Vite now, with `assets/` still shared for tokens/CSS/markdown.
- Clarify `./verify` includes frontend gates and e2e, not only Python.
- Fix docs convention pointer: it says `docs/AGENTS.md`, but the actual convention file is `docs/CLAUDE.md`.
- Clarify `data/` is runtime-only and gitignored.
- Clarify worker authority: CLI worker commands file proposals/recaps/notes; canonical state/value transitions stay in backend writers/resolution engine.
- Clarify skill locations and which are active runtime role skills.

## What `CLAUDE.md` should include or clarify

`CLAUDE.md` should mostly point to `AGENTS.md`, not duplicate it. It is currently a near-copy and already drifted. Keep Claude-specific deltas only if needed, for example docs convention loading or Claude-specific tool behavior.

Current stale/wrong items:

- Says server command is `plan serve`; actual is `panels serve`.
- Says frontend is no-build vanilla JS in `assets/`; actual is Svelte/Vite in `web/`.
- `AGENTS.md` says `docs/AGENTS.md`; actual file is `docs/CLAUDE.md`.
- `skills/panels/SKILL.md` mentions `panels-rollover` and `panels-sprint-planning`, but Descartes did not find those local skill files.
- The `/codex-cli` skill reference in top-level guidance is not backed by a visible repo skill under `skills/`; if it is external/global, say that explicitly.

## Concrete snippets

Replace `CLAUDE.md` with a pointer-style file:

```md
# CLAUDE.md

Read `AGENTS.md` first. It is the canonical top-level assistant guidance for this repo.

Claude-specific note: when working under `docs/`, also read `docs/CLAUDE.md`; it defines the plain-language documentation conventions.

Do not duplicate repo structure, commands, verification rules, or operating model here. Keep those in `AGENTS.md` so assistant guidance does not drift.
```

Add this to `AGENTS.md` under "Project notes" or as a new "Repo map" section:

```md
## Repo map

- `src/planner/` is the Python 3.12 package. FastAPI wiring lives in `core/server.py`; SQLite schema and migrations live in `core/db.py`.
- Domain code is grouped by system: `tickets/`, `sprints/`, `days/`, `projects/`, `chat/`. Contracts live in each domain's `contracts.py`; framework-free rules live in `logic/`; HTTP routes live in `api.py`.
- `panels` is the CLI entry point (`planner.cli.main:main`). `python -m planner` delegates to the same command tree. Important groups are `serve`, `project`, `day`, `ticket`, `sprint`, `sprint item`, and `worker`.
- The runtime is split between `runtime/system_a.py` and `runtime/system_b.py`: System A polls runnable tickets on today's board; System B runs one ticket step through the shared Hermes gateway.
- Gateway and chat code lives in `minds/` and `chat/`. The shared gateway owns Hermes session transport; chat exposes human sends, streaming, command catalog, and history.
- The frontend is Svelte/Vite in `web/`. FastAPI serves the built `web/dist` app at `/` and Vite chunks under `/_app/`.
- Shared design/runtime assets remain in `assets/`: `tokens.css`, `app.css`, and `markdown.js`.
- Local agent role skills live in `skills/`, especially `panels`, `panels-worker`, and `panels/panels-ticket-work`.
- `docs/` is the live plain-language system documentation. `docs/README.md` is the map; `docs/CLAUDE.md` is the docs-writing convention.
- `orchestration/*-redesign/` holds current design intent and mockups; `orchestration/tickets/` holds ticket plans, dispatches, and reviews.
- `data/` is gitignored runtime state: SQLite DBs, WAL/SHM files, logs, locks, Hermes home state, smoke artifacts, and verify output.
```

Replace the current `AGENTS.md` docs bullet with:

```md
- **docs/** — plain-language documentation of what exists and how it works, split by system (`docs/README.md` is the map; `docs/CLAUDE.md` holds the conventions). Keep it current, not historical.
```

Replace or add the command/frontend verification notes:

```md
- Run the server with `panels serve` or `python -m planner serve`.
- Run the frontend checks with `npm --prefix web run check`, build with `npm --prefix web run build`, and run the frontend event-mapping test with `npm --prefix web test`.
- `./verify` is the completeness gate: ruff, mypy, unit tests, asset syntax checks, frontend check/build/test, and Playwright-backed e2e tests.
```

Add this skills clarification:

```md
## Skills and worker roles

The server provisions local role skills into the configured Hermes home at startup. The active repo skills are under `skills/`: `panels` for system orientation, `panels-worker` for automatic ticket work, and `panels/panels-ticket-work` for gated ticket proposal flow. Worker commands must not approve or directly write canonical ticket values; they file proposals, recaps, notes, and item-status proposals through `panels worker ...`.
```
