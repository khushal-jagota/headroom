# DOGFOOD.md — the Section 18.5 fake-ticket dogfood record

Three escalating levels. Levels B and C are the one sanctioned exception to the fakes-only rule: they use the real local Hermes runtime, never inside `./verify`. Narrative without corresponding logs is not evidence; every claim below must point at an artifact (event-log excerpt, run row, file under `data/logs/`).

## Level A — the CLI as a user and agent would drive it (item 35, gating)

Status: **not yet run.** Will be `scripts/dogfood_cli.py` executed inside `./verify` as `test_e35_dogfood_level_a` (stage 7, ticket T21). Evidence when done: the verify scoreboard line for item 35, plus the script's step-by-step PASS trace.

## Level B — a Hermes agent works a fake ticket

Status: **not yet run.** Planned shape per §18.5: demo-seeded server; one ticket shaped ceiling `needs_plan` / `at_cap = propose`; one real session `hermes chat -q "Read <repo>/skills/planning-worker.md, then work planning ticket <id> using the plan CLI"` with `PLAN_SERVER_URL` + `PLAN_TICKET_ID` pinned; session output captured under `data/logs/`. Pass evidence (mechanical): ≥1 proposal write and ≥1 recap write by the session on that ticket, shown from the event log; the log file path recorded here.

Attempts log (three-attempt rule, materially different each time):

_(none yet)_

## Level C — a dispatched ticket agent end to end

Status: **not yet run.** Planned shape per §18.5: real spawn adapter enabled via config; one eligible demo ticket; real dispatcher tick; claim → run row → spawned agent writes ≥1 proposal carrying the claim env → run closes with a terminal outcome → ticket lands where its ceiling dictates. Evidence: the run row, the event sequence, the per-run log path under `data/logs/`.

Attempts log:

_(none yet)_

## Staleness rule

If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run. Evidence below always names the git commit of the tree it was produced on.
