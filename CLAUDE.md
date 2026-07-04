# CLAUDE.md

The specification for this project lives in **SPEC.md**. It is the single source of truth. Never modify it.

**PRINCIPLES.md** holds the standing engineering and design rules; they bind everywhere SPEC.md doesn't explicitly override them. Never modify it either.

This repo is the v2 planning system (Python/FastAPI + SQLite + no-build JS). It is built and tested entirely inside this repository: never read from or write to `~/.hermes/planning/` (the live markdown planning system), `~/.hermes/hermes-agent/`, or any other repo. External boundaries (hermes spawn, boundary/replan agent, chat gateway) are adapters with fakes; tests use the fakes, always.

## Memory
- **PROGRESS.md** — update every work cycle: current build stage, what just passed, current hypothesis, next step, blockers. After any context compaction, read it first — it is your memory, not the conversation.
- **decisions.md** — every delegated or judgment call, briefly justified.
- **DOCS.md** — plain-language documentation of what exists and how it works, written progressively as work completes, never retrofitted. Simple sentences, no jargon. A smart non-engineer must be able to read it. If a section can't be understood without reading the code, rewrite the section.

## Verification
- `./verify` is the only source of truth for completeness. Run it fresh before any claim of progress and show the full output. Never assert results from memory or quote an earlier run.
- Browser behavior is asserted through the Playwright e2e suite inside `./verify` — never eyeballed.
- The Codex CLI is the independent reviewer. Use it wherever a second pair of eyes beats self-review: auditing completed work against the spec, reviewing intricate logic (the resolution engine, dispatch eligibility, planning-date math), checking a diff before integration. Invoke it non-interactively — `codex exec "..."` — pointing it at specific files plus the relevant SPEC.md section, asking for concrete violations. Surface its full output, then address or refute each point in writing before moving on.

## Operating model: plan and orchestrate
- You — the agent holding the goal condition — are primarily a **planner and orchestrator of sub-agents**. Your own outputs are: the plan, contract-scoped tickets, dispatches, independent reviews, serial integrations, verification runs, and the memory files. Implementation substance is produced by sub-agents working tickets.
- Write code directly only when a change is too small to be worth a ticket — glue, integration repairs, one-line fixes — and note it in PROGRESS.md. If you catch yourself implementing a stage's substance inline, stop and cut tickets. Route, don't execute.
- A ticket is contract-scoped: it names the contract/type files it implements against, the acceptance tests it must turn green, and nothing else. Sub-agents do not invent shapes, do not modify contracts, and do not touch files outside their ticket.
- Per-ticket pipeline — each step isolated work: (1) you decompose and write the ticket; (2) a sub-agent plans the ticket's implementation; (3) Codex reviews that plan against the contracts and the relevant SPEC.md section; (4) a sub-agent implements to the reviewed plan; (5) Codex reviews the implementation diff; (6) you integrate serially and run full `./verify`. Steps 2–5 can be collapsed only for trivial tickets, noted in decisions.md.
- Parallelisation is your call: decide from file overlap which tickets may share the main worktree and which need isolated git worktrees; never let two agents write the same files concurrently.
- Spot-check the load-bearing code yourself even when reviews pass: the resolution engine, dispatcher claim/reclaim, planning-date math, and the migration parser.
- A ticket is done when its named tests pass through `./verify` and its Codex reviews report no violations.

## Conduct
- Follow the build order in SPEC.md Section 18. Never advance over failing tests.
- Blocked three attempts on the same problem → log it in PROGRESS.md and change approach materially, not the same idea harder.
- Do not ask questions mid-run. Delegated choices are yours; make them and log them in decisions.md.

## Project notes
- Python ≥ 3.12, venv at `.venv`, deps pinned in `requirements.txt`. Run the server: `plan serve` (or `python -m planner serve`). DB and logs live under `data/` (gitignored).
- Frontend is no-build vanilla JS in `assets/` — classic scripts, no bundler; syntax-check with `node --check`.
- The event feed is an invalidation signal; the UI refetches JSON. Do not build client-side state stores.
- One canonical writer function per state transition. Agents (claim-carrying requests) write proposals only — the resolution engine is the single door to canonical values.
