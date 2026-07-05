# DOGFOOD.md — the Section 18.5 fake-ticket dogfood record

Three escalating levels. Levels B and C are the one sanctioned exception to the fakes-only rule: they use the real local Hermes runtime, never inside `./verify`. Narrative without corresponding logs is not evidence; every claim below must point at an artifact (event-log excerpt, run row, file under `data/logs/`). Because `data/` is gitignored, durable copies of the load-bearing artifacts — the session and per-run logs, server-log tails, and full event-row dumps from both dogfood databases — are tracked in `orchestration/dogfood-evidence/`.

## Level A — the CLI as a user and agent would drive it (item 35, gating)

Status: **PASS, inside `./verify`.** `scripts/dogfood_cli.py` drives the full workflow against a demo-seeded test server — agent actions through the real CLI subprocess, human actions through the HTTP API — creating a ticket and taking it through every gate with every accept carrying its grant pair, including one edit-accept and one superseded proposal, recap written and overwritten, day assign/remove, a blocking link proven dispatch-ineligible then cleared, a claim via the test dispatcher tick, a result proposed with claim env, and the needs_review approval to done; each step's resulting state asserted from `--json`/response output, non-zero exit on first mismatch, step-by-step PASS trace printed. It runs as `test_e35_dogfood_level_a` in `tests/e2e/test_dogfood.py`. Evidence: the `[PASS] item 35 — dogfood Level A` scoreboard line in `orchestration/verify-runs/003-stage7-fix.md` (VERIFY: 36/36 PASS, exit 0) and in every subsequent verify run.

## Level B — a Hermes agent works a fake ticket

Status: **PASS — attempt 1** (2026-07-05, tree `3dc243b0729c0485ae21bba2e8039d49a7a6bc8f`). History, per the §18.5 staleness rule: passed cleanly in every prior round — `7118677` (the run that produced the guardrail sentence, after a session there ran `./verify` unprompted and deleted an untracked operator directory), `b52d836`, `f7db367`, and `528ec7c`; this run supersedes all four after round 5 added two more `reject_agents` gates (`POST /sprints/{id}/addenda`, `POST /chat/{id}/send`) — neither on the dogfood path.

### What was run

1. Server (no test mode, dispatch off; a long tick keeps the boundary loop from re-creating today's day during the seed reset):
   ```
   PLAN_DB_PATH=data/dogfood-b5.db PLAN_PORT=8799 PLAN_DISPATCH_ENABLED=0 PLAN_TICK_SECONDS=3600 .venv/bin/plan serve
   ```
   Server log: `data/logs/level-b5-server.log` (tracked tail: `orchestration/dogfood-evidence/level-b-server-tail.log`).
2. Seed wrinkle (same in every round, disclosed): on startup the real boundary job had already created a day row plus its bootstrap events and a `boundary_runs` row, so `plan seed --demo` refused with `db_not_empty` (naming the `days` table). As the human operator I cleared those three bootstrap tables (`DELETE FROM days; DELETE FROM events; DELETE FROM boundary_runs;` via sqlite3 on the scratch DB) and re-ran the seed, which then reported `{"sprints": 1, "sprint_items": 3, "tickets": 8, "links": 2, ...}`.
3. Ticket shaping (human action, grant route): the `needs_success` demo ticket `t_j68zj895` "Draft the onboarding email success criteria." was raised via `POST /api/tickets/t_j68zj895/grant {"ceiling":"needs_plan","at_cap":"propose"}` (event 23, `cause: human_grant`). The request carried no `X-Plan-*` headers, so §7.6 classified it as the human — the write-surface gating (`reject_agents`) admits it and would reject the same route from an agent.
4. One real session, env pinned, all output captured — stock §18.5 prompt plus the standing guardrail sentence:
   ```
   PLAN_SERVER_URL=http://127.0.0.1:8799 PLAN_TICKET_ID=t_j68zj895 \
     hermes chat -q "Read /Users/khushaljagota/.hermes/planning-v2/skills/planning-worker.md, then work planning ticket t_j68zj895 using the plan CLI. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." \
     > data/logs/level-b5-attempt1.log 2>&1
   ```
   The session exited 0 (duration 1m10s). Session log: `data/logs/level-b5-attempt1.log` (tracked copy: `orchestration/dogfood-evidence/level-b-attempt1.log`).

### Evidence (event log, `data/dogfood-b5.db`, also served by `GET /api/tickets/t_j68zj895/events`; full event-row dump tracked at `orchestration/dogfood-evidence/level-b-events.txt` — 9 rows total on the ticket)

```
id  kind               payload (truncated)                                          local time (2026-07-05)
8   ticket_created     {"title": "Draft the onboarding email success criteria." …}  22:04:26
23  grant_changed      {"ceiling": "needs_plan", "at_cap": "propose", "cause": "human_grant"}  22:04:26
24  proposal_accepted  {"field": "success", …}                                       22:05:25
25  state_changed      {"from": "needs_success", "to": "needs_approach", "cause": "auto_accept"}  22:05:25
26  recap_updated      {}                                                            22:05:31
27  proposal_accepted  {"field": "approach", …}                                      22:05:31
28  state_changed      {"from": "needs_approach", "to": "needs_plan", "cause": "auto_accept"}  22:05:31
29  proposal_filed     {"field": "plan", …, "proposed_by": "agent"}                  22:05:40
30  recap_updated      {}                                                            22:05:40
```

Proposal writes by the session: 3 (events 24, 27 accepted; 29 filed — carrying `"proposed_by": "agent"`, the §7.6 non-dispatched attribution). Recap writes: 2 (events 26, 30 — the session recapped after success and after the parked plan, not after approach; the requirement is ≥1 of each, satisfied). Final ticket state (verified through `GET /api/tickets/t_j68zj895`): `needs_plan` / ceiling `needs_plan` / at_cap `propose` — success and approach accepted below the ceiling, the plan proposal parked at the ceiling for human review, exactly as `at_cap = propose` dictates. The Level B server log shows no `400 agent_forbidden`; its only 4xx is the deliberate `db_not_empty` seed probe.

### Attempts log (three-attempt rule)

- **Attempt 1 — PASS.** Stock prompt shape plus guardrail. The session found `.venv/bin/python -m planner`, oriented with `ticket show --json`, and worked the ticket to its ceiling. No further attempts needed.

### Conduct

The session stayed inside its brief: its closing message states "I did not run ./verify. I did not modify/delete repository files," and a scoped tree check confirmed the session touched no tracked files (the DB and logs are gitignored). The working tree's `decisions.md` edit is the lead's own concurrent round-5 note, not the session's. The guardrail sentence has held in every guardrailed run.

## Level C — a dispatched ticket agent end to end

Status: **PASS — wrapper shim path, single run** (2026-07-05, tree `3dc243b0729c0485ae21bba2e8039d49a7a6bc8f`). History, per the §18.5 staleness rule: passed in every prior round through the shim — `7118677` (attempt 1 there died on the stock spawn command's `Error: Unknown skill(s): planning-worker`, since that skill is deliberately not installed in `~/.hermes`; attempt 2 passed through the shim, two runs), `b52d836` (shim, two runs), `f7db367` (shim, single run), and `528ec7c` (shim, two runs). The stock-command failure mode is environmental and untouched by round 5 (which added two `reject_agents` gates on `POST /sprints/{id}/addenda` and `POST /chat/{id}/send`, neither on the spawn/claim/propose path), so this round went straight to the shim per the lead's instruction; the stock failure remains honestly recorded from the first round.

### Setup

Two-phase start so the dispatcher could not claim mid-shaping. Phase 1 — shaping (dispatch off, long tick): `PLAN_DB_PATH=data/dogfood-c5.db PLAN_PORT=8800 PLAN_DISPATCH_ENABLED=0 PLAN_TICK_SECONDS=3600 .venv/bin/plan serve` (shaping log: `data/logs/level-c5-shaping.log`), seed `--demo` (same `db_not_empty` bootstrap-row wrinkle as Level B, cleared the same way), then shaping via the grant route (human actions, no `X-Plan-*` headers):

- Target `t_cj6dp2e3` "Draft the onboarding email success criteria." (state `needs_success`) granted ceiling `in_progress`, at_cap `propose`.
- The three other dispatch-eligible demo tickets stop-capped at their current states: `t_fg48wu8n` → (`in_progress`, `stop`), `t_pyv2fwex` → (`needs_approach`, `stop`), `t_a7en2xr9` → (`needs_plan`, `stop`). The rest were already ineligible by state.
- Verified with `plan queue pickup --json`: exactly one entry, `t_cj6dp2e3`.

Then the shaping server was killed and the same DB relaunched with real dispatch through the disclosed shim: `PLAN_DB_PATH=data/dogfood-c5.db PLAN_PORT=8800 PLAN_DISPATCH_ENABLED=1 PLAN_TICK_SECONDS=20 PLAN_MAX_RUNS=1 PLAN_HERMES_BIN=orchestration/dogfood/hermes-wrapped.sh .venv/bin/plan serve` (log: `data/logs/level-c5-server.log`; tracked tail: `orchestration/dogfood-evidence/level-c-server-tail.log`). No test mode; `spawn_adapter: auto` resolved to the real adapter. The shim (`orchestration/dogfood/hermes-wrapped.sh`, sanctioned §18.5 "prompt shape" latitude) execs the real hermes with the unresolvable `--skills planning-worker` flag dropped and the message enriched to "Read <repo>/skills/planning-worker.md, then work planning ticket <id>. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." Nothing in `src/` changed; the shim only rewrites the spawn command line.

### Evidence (runs table + event log, `data/dogfood-c5.db`; full event-row + run-row dump tracked at `orchestration/dogfood-evidence/level-c-events.txt` — 17 rows total on the ticket, 15 of them the dispatch chain)

Run (`SELECT id,ticket_id,status,started_at,ended_at,pid FROM runs` — exactly one row):

```
run_n87whdsj  t_cj6dp2e3  done  22:07:49 → 22:09:49  pid 28696  summary: "Brought ticket t_cj6dp2e3 from needs_success to in_progress. Success, approach, and plan were accepted; result parked…"
```

Event sequence on `t_cj6dp2e3` (all times 2026-07-05 local; the dispatcher claimed on its first tick):

```
28  run_started       {"run_id": "run_n87whdsj"}                                    22:07:49
29  claim_heartbeat   {"run_id": "run_n87whdsj", …}                                 22:08:24
30  claim_heartbeat   {"run_id": "run_n87whdsj", …}                                 22:09:48
31  proposal_accepted {"field": "success", …}                                       22:09:48
32  state_changed     needs_success → needs_approach (auto_accept)                  22:09:48
33  recap_updated                                                                   22:09:48
34  proposal_accepted {"field": "approach", …}                                      22:09:48
35  state_changed     needs_approach → needs_plan (auto_accept)                     22:09:48
36  recap_updated                                                                   22:09:48
37  proposal_accepted {"field": "plan", …}                                          22:09:48
38  state_changed     needs_plan → in_progress (auto_accept) — the ceiling          22:09:48
39  recap_updated                                                                   22:09:48
40  proposal_filed    {"field": "result", …, "proposed_by": "agent"}               22:09:48   — parks at the ceiling
41  recap_updated                                                                   22:09:49
42  run_closed        {"run_id": "run_n87whdsj", "status": "done", …}               22:09:49
```

Chain kind counts: 1 run_started, 2 claim_heartbeat, 3 proposal_accepted, 3 state_changed, 4 recap_updated, 1 proposal_filed, 1 run_closed. (The worker composed the whole ladder first, then fired the CLI writes in quick succession — events 31–40 share one second — after two heartbeat ticks during its thinking phase.) Every write was made under the active claim — the authctx claim validation (`validate_carried_claim` → `require_claim`) would otherwise have rejected it, and the dispatch server log is 200 on every request — no `400`/`agent_forbidden` anywhere in the chain; the heartbeats name the run id.

This round produced the **single-run shape** (as at `f7db367`; `528ec7c` was the two-run shape — DOGFOOD.md records both as equally valid): the worker filed the parked `result` proposal inside the same run before closing, so the ticket left the run already ineligible (pending proposal on the gating field) and **no second dispatch was needed** — one run, the complete claim → heartbeat → propose ×4 → recap → close chain. Confirmed: after the run closed, `plan queue pickup --json` returned `{"pickup": []}` and the runs count held at 1 across 2+ further ticks — no re-dispatch. The two-run shape (a run closing at the ceiling without proposing `result`, the next tick correctly re-claiming) is equally valid; both terminate by design, not by luck.

Final ticket state (`GET /api/tickets/t_cj6dp2e3`): state `in_progress` = ceiling, `at_cap propose`, success/approach/plan accepted, result proposal pending for human review, `auto_blocked false`, `consecutive_failures 0`. The ticket landed exactly where its ceiling dictates.

Per-run log under `data/logs/` (tracked copy in `orchestration/dogfood-evidence/`): `run_n87whdsj.log`. The session respected the guardrail (closing message: "I did not run ./verify and did not modify/delete repository files."); the working tree was clean after the entire re-run, apart from the lead's own `decisions.md` note, which is a concurrent round-5 edit.

### Systemic findings (observed at the `7118677` round; finding #1 fixed in round 4, finding #2 still open — worth a ticket)

1. **Dead spawn children are invisible to the pid sweep until claim-TTL expiry.** *FIXED this audit round (round 4, post-`f7db367` freeze).* Observed directly at `7118677` and still present through the `f7db367` freeze: the server never reaped its spawned Popen children, so a crashed hermes became a zombie, and `_pid_alive`'s `os.kill(pid, 0)` reported zombies alive — the dead-pid reclaim could not fire while the spawning server lived; the run sat `running` up to 15 minutes (at `7118677` the reclaim then fired only because a server restart let the OS reap the zombie). The fix: `RealSpawnAdapter` now retains each spawned child's `Popen` handle. Its `is_pid_alive` probes a tracked pid with `Popen.poll()` — `None` means still running, a returncode means the child has exited and is reaped on the spot, so it reports dead — and the dispatcher's real-mode reclaim sweep uses that method (the same adapter instance that spawned the children) as its liveness probe, so a dead worker is now detected and reclaimed within one tick instead of at TTL. A companion `reap_finished_children`, called once per tick after the sweep, reaps and drops the handles of children whose runs closed by any other path (the sweep never probes those pids), so no exited child lingers as a zombie and the handle map stays bounded. Untracked pids (e.g. a claim carried across a server restart) still fall back to the signal-0 `_pid_alive`.
2. **Instant-crash loops never trip the circuit breaker.** A dead-pid/expired reclaim closes the run `reclaimed`, which D6 deliberately exempts from `consecutive_failures` — so a hermes that dies instantly on every spawn (e.g. the missing-skill error) is reclaimed and respawned every tick indefinitely, with nothing to stop it.

Audit rounds 1–3 changed request-body parsing in the api.py files, not the spawn adapter (`src/planner/core/adapters/real.py`) or the reclaim sweep (`src/planner/dispatch/data.py`), which is why both findings survived them unchanged. Round 4 fixes finding #1 in the spawn adapter and the reclaim sweep's liveness probe (see above); finding #2 remains as a noted, spec-correct observation (§7.5/D6).

Cleanup: both re-run servers killed; no spawned hermes sessions left. The pre-existing `t09-smoke` `plan serve` (pid 43519, temp-directory DB, not started by the dogfood) remains for the lead to dispose of.

## Staleness rule

If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run. Evidence below always names the git commit of the tree it was produced on.
