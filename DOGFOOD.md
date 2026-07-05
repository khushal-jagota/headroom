# DOGFOOD.md — the Section 18.5 fake-ticket dogfood record

Three escalating levels. Levels B and C are the one sanctioned exception to the fakes-only rule: they use the real local Hermes runtime, never inside `./verify`. Narrative without corresponding logs is not evidence; every claim below must point at an artifact (event-log excerpt, run row, file under `data/logs/`). Because `data/` is gitignored, durable copies of the load-bearing artifacts — the session and per-run logs, server-log tails, and full event-row dumps from both dogfood databases — are tracked in `orchestration/dogfood-evidence/`.

## Level A — the CLI as a user and agent would drive it (item 35, gating)

Status: **PASS, inside `./verify`.** `scripts/dogfood_cli.py` drives the full workflow against a demo-seeded test server — agent actions through the real CLI subprocess, human actions through the HTTP API — creating a ticket and taking it through every gate with every accept carrying its grant pair, including one edit-accept and one superseded proposal, recap written and overwritten, day assign/remove, a blocking link proven dispatch-ineligible then cleared, a claim via the test dispatcher tick, a result proposed with claim env, and the needs_review approval to done; each step's resulting state asserted from `--json`/response output, non-zero exit on first mismatch, step-by-step PASS trace printed. It runs as `test_e35_dogfood_level_a` in `tests/e2e/test_dogfood.py`. Evidence: the `[PASS] item 35 — dogfood Level A` scoreboard line in `orchestration/verify-runs/003-stage7-fix.md` (VERIFY: 36/36 PASS, exit 0) and in every subsequent verify run.

## Level B — a Hermes agent works a fake ticket

Status: **PASS — attempt 1** (2026-07-05, tree `528ec7c0fec5829956c2e0c7bad5ad37ea7eaf51`). History, per the §18.5 staleness rule: passed cleanly in every prior round — `7118677` (the run that produced the guardrail sentence, after a session there ran `./verify` unprompted and deleted an untracked operator directory), `b52d836`, and `f7db367`; this run supersedes all three after round 4 changed the spawn adapter (dead-worker detection) and the api.py agent write-surface gating.

### What was run

1. Server (no test mode, dispatch off; a long tick keeps the boundary loop from re-creating today's day during the seed reset):
   ```
   PLAN_DB_PATH=data/dogfood-b4.db PLAN_PORT=8797 PLAN_DISPATCH_ENABLED=0 PLAN_TICK_SECONDS=3600 .venv/bin/plan serve
   ```
   Server log: `data/logs/level-b4-server.log` (tracked tail: `orchestration/dogfood-evidence/level-b-server-tail.log`).
2. Seed wrinkle (same in every round, disclosed): on startup the real boundary job had already created a day row plus its bootstrap events and a `boundary_runs` row, so `plan seed --demo` refused with `db_not_empty` (naming the `days` table). As the human operator I cleared those three bootstrap tables (`DELETE FROM days; DELETE FROM events; DELETE FROM boundary_runs;` via sqlite3 on the scratch DB) and re-ran the seed, which then reported `{"sprints": 1, "sprint_items": 3, "tickets": 8, "links": 2, ...}`.
3. Ticket shaping (human action, grant route): the `needs_success` demo ticket `t_uzqkw800` "Draft the onboarding email success criteria." was raised via `POST /api/tickets/t_uzqkw800/grant {"ceiling":"needs_plan","at_cap":"propose"}` (event 23, `cause: human_grant`). The request carried no `X-Plan-*` headers, so §7.6 classified it as the human — the round-4 gating (`reject_agents`) admits it and would reject the same route from an agent.
4. One real session, env pinned, all output captured — stock §18.5 prompt plus the standing guardrail sentence:
   ```
   PLAN_SERVER_URL=http://127.0.0.1:8797 PLAN_TICKET_ID=t_uzqkw800 \
     hermes chat -q "Read /Users/khushaljagota/.hermes/planning-v2/skills/planning-worker.md, then work planning ticket t_uzqkw800 using the plan CLI. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." \
     > data/logs/level-b4-attempt1.log 2>&1
   ```
   The session exited 0. Session log: `data/logs/level-b4-attempt1.log` (tracked copy: `orchestration/dogfood-evidence/level-b-attempt1.log`).

### Evidence (event log, `data/dogfood-b4.db`, also served by `GET /api/tickets/t_uzqkw800/events`; full event-row dump tracked at `orchestration/dogfood-evidence/level-b-events.txt` — 10 rows total on the ticket)

```
id  kind               payload (truncated)                                          local time (2026-07-05)
8   ticket_created     {"title": "Draft the onboarding email success criteria." …}  21:20:45
23  grant_changed      {"ceiling": "needs_plan", "at_cap": "propose", "cause": "human_grant"}  21:20:56
24  proposal_accepted  {"field": "success", "body": "Success means we have a concise, usable definition …"}  21:22:22
25  state_changed      {"from": "needs_success", "to": "needs_approach", "cause": "auto_accept"}  21:22:22
26  recap_updated      {}                                                            21:22:25
27  proposal_accepted  {"field": "approach", "body": "Approach:\n\n1. Treat the onboarding email as a behavi…"}  21:22:30
28  state_changed      {"from": "needs_approach", "to": "needs_plan", "cause": "auto_accept"}  21:22:30
29  recap_updated      {}                                                            21:22:35
30  proposal_filed     {"field": "plan", "body": "Plan proposal:\n…", "proposed_by": "agent"}  21:22:51
31  recap_updated      {}                                                            21:22:56
```

Proposal writes by the session: 3 (events 24, 27 accepted; 30 filed — carrying `"proposed_by": "agent"`, the §7.6 non-dispatched attribution). Recap writes: 3 (events 26, 29, 31). Requirement was ≥1 of each. Final ticket state (verified through `GET /api/tickets/t_uzqkw800`): `needs_plan` / ceiling `needs_plan` / at_cap `propose` — success and approach accepted below the ceiling, the plan proposal parked at the ceiling for human review, exactly as `at_cap = propose` dictates. The Level B server log shows no `400 agent_forbidden`; its only 4xx is the deliberate `db_not_empty` seed probe.

### Attempts log (three-attempt rule)

- **Attempt 1 — PASS.** Stock prompt shape plus guardrail. The session found `.venv/bin/python -m planner`, oriented with `ticket show --json`, and worked the ticket to its ceiling. No further attempts needed.

### Conduct

The session stayed inside its brief: its closing message states "I did not run ./verify and did not modify/delete repository files," and a scoped tree check confirmed the session touched no tracked files (the DB and logs are gitignored). The working tree's `PROGRESS.md`/`decisions.md` edits are the lead's own round-4 orchestration notes, written before the session began — their mtimes precede the seed. The guardrail sentence has held in every guardrailed run.

## Level C — a dispatched ticket agent end to end

Status: **PASS — wrapper shim path, two runs** (2026-07-05, tree `528ec7c0fec5829956c2e0c7bad5ad37ea7eaf51`). History, per the §18.5 staleness rule: passed in every prior round through the shim — `7118677` (attempt 1 there died on the stock spawn command's `Error: Unknown skill(s): planning-worker`, since that skill is deliberately not installed in `~/.hermes`; attempt 2 passed through the shim, two runs), `b52d836` (shim, two runs), and `f7db367` (shim, single run). The stock-command failure mode is environmental and untouched by round 4 (which changed the spawn adapter's dead-worker detection and the api.py agent write-surface gating, not the `--skills` resolution), so this round went straight to the shim per the lead's instruction; the stock failure remains honestly recorded from the first round.

### Setup

Two-phase start so the dispatcher could not claim mid-shaping. Phase 1 — shaping (dispatch off, long tick): `PLAN_DB_PATH=data/dogfood-c4.db PLAN_PORT=8798 PLAN_DISPATCH_ENABLED=0 PLAN_TICK_SECONDS=3600 .venv/bin/plan serve` (shaping log: `data/logs/level-c4-shaping.log`), seed `--demo` (same `db_not_empty` bootstrap-row wrinkle as Level B, cleared the same way), then shaping via the grant route (human actions, no `X-Plan-*` headers):

- Target `t_882y9p08` "Draft the onboarding email success criteria." (state `needs_success`) granted ceiling `in_progress`, at_cap `propose`.
- The three other dispatch-eligible demo tickets stop-capped at their current states: `t_mw1jwt1d` → (`in_progress`, `stop`), `t_pdxh5pfm` → (`needs_approach`, `stop`), `t_9f60zsae` → (`needs_plan`, `stop`). The rest were already ineligible by state.
- Verified with `plan queue pickup --json`: exactly one entry, `t_882y9p08`.

Then the shaping server was killed and the same DB relaunched with real dispatch through the disclosed shim: `PLAN_DB_PATH=data/dogfood-c4.db PLAN_PORT=8798 PLAN_DISPATCH_ENABLED=1 PLAN_TICK_SECONDS=20 PLAN_MAX_RUNS=1 PLAN_HERMES_BIN=orchestration/dogfood/hermes-wrapped.sh .venv/bin/plan serve` (log: `data/logs/level-c4-server.log`; tracked tail: `orchestration/dogfood-evidence/level-c-server-tail.log`). No test mode; `spawn_adapter: auto` resolved to the real adapter. The shim (`orchestration/dogfood/hermes-wrapped.sh`, sanctioned §18.5 "prompt shape" latitude) execs the real hermes with the unresolvable `--skills planning-worker` flag dropped and the message enriched to "Read <repo>/skills/planning-worker.md, then work planning ticket <id>. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." Nothing in `src/` changed; the shim only rewrites the spawn command line.

### Evidence (runs table + event log, `data/dogfood-c4.db`; full event-row + run-row dump tracked at `orchestration/dogfood-evidence/level-c-events.txt` — 19 rows total on the ticket, 17 of them the dispatch chain across two runs)

Runs (`SELECT id,ticket_id,status,started_at,ended_at,pid FROM runs` — two rows, both `done`):

```
run_ppmwezf0  t_882y9p08  done  21:26:33 → 21:28:15  pid 19430  summary: "Reached ceiling for t_882y9p08. Success, approach, and plan were proposed and ac…"
run_uyzeq3hd  t_882y9p08  done  21:28:33 → 21:30:19  pid 19764  summary: "Worked t_882y9p08 to its in_progress ceiling … result parked as a proposal for review."
```

Event sequence on `t_882y9p08` (all times 2026-07-05 local; the dispatcher claimed on its first tick):

```
28  run_started       {"run_id": "run_ppmwezf0"}                                    21:26:33
29  claim_heartbeat   {"run_id": "run_ppmwezf0", …}                                 21:27:24
30  proposal_accepted {"field": "success", "body": "Success means the ticket has a concrete, reviewable set…"}  21:27:47
31  state_changed     needs_success → needs_approach (auto_accept)                  21:27:47
32  recap_updated                                                                   21:27:51
33  proposal_accepted {"field": "approach", "body": "Approach: treat this as a content strategy artifact, n…"}  21:27:57
34  state_changed     needs_approach → needs_plan (auto_accept)                     21:27:57
35  recap_updated                                                                   21:28:01
36  proposal_accepted {"field": "plan", "body": "1. State the email's single job in one sentence.\n2. Defin…"}  21:28:07
37  state_changed     needs_plan → in_progress (auto_accept) — the ceiling          21:28:07
38  recap_updated                                                                   21:28:12
39  run_closed        {"run_id": "run_ppmwezf0", "status": "done", …}               21:28:15   — closes at the ceiling without proposing result
40  run_started       {"run_id": "run_uyzeq3hd"}                                    21:28:33   — dispatcher re-claims on the next tick
41  claim_heartbeat   {"run_id": "run_uyzeq3hd", …}                                 21:29:50
42  proposal_filed    {"field": "result", …, "proposed_by": "agent"}               21:30:08   — parks at the ceiling
43  recap_updated                                                                   21:30:15
44  run_closed        {"run_id": "run_uyzeq3hd", "status": "done", …}               21:30:19
```

Chain kind counts: 2 run_started, 2 claim_heartbeat, 3 proposal_accepted, 3 state_changed, 4 recap_updated, 1 proposal_filed, 2 run_closed. Every write was made under the active claim — the round-4 authctx claim validation (`validate_carried_claim` → `require_claim`) would otherwise have rejected it, and the dispatch server log shows no `400`/`agent_forbidden` anywhere in the chain; each run's heartbeat names its own run id.

This round reproduced the **two-run shape** (vs the single run at `f7db367`), which DOGFOOD.md records as equally valid: run 1 auto-accepted success/approach/plan up to the `in_progress` ceiling and closed `done` without filing `result`, leaving the ticket still eligible (at_cap `propose` at the ceiling), so the dispatcher correctly re-claimed on the next tick; run 2 filed the parked `result` proposal and closed, leaving the ticket ineligible (pending proposal on the gating field). Confirmed: after run 2 closed, `plan queue pickup --json` returned `{"pickup": []}` and the runs count held at 2 across 2+ further ticks — no third dispatch. Both terminate by design, not by luck.

Final ticket state (`GET /api/tickets/t_882y9p08`): state `in_progress` = ceiling, `at_cap propose`, success/approach/plan accepted, result proposal pending for human review, `auto_blocked false`, `consecutive_failures 0`. The ticket landed exactly where its ceiling dictates.

Per-run logs under `data/logs/` (tracked copies in `orchestration/dogfood-evidence/`): `run_ppmwezf0.log`, `run_uyzeq3hd.log`. Both sessions respected the guardrail (run 2's closing message: "Did not run ./verify. Did not modify or delete repository files."); the working tree was clean after the entire re-run, apart from the lead's own `PROGRESS.md`/`decisions.md` notes, which predate the runs.

### Systemic findings (observed at the `7118677` round; finding #1 fixed in round 4, finding #2 still open — worth a ticket)

1. **Dead spawn children are invisible to the pid sweep until claim-TTL expiry.** *FIXED this audit round (round 4, post-`f7db367` freeze).* Observed directly at `7118677` and still present through the `f7db367` freeze: the server never reaped its spawned Popen children, so a crashed hermes became a zombie, and `_pid_alive`'s `os.kill(pid, 0)` reported zombies alive — the dead-pid reclaim could not fire while the spawning server lived; the run sat `running` up to 15 minutes (at `7118677` the reclaim then fired only because a server restart let the OS reap the zombie). The fix: `RealSpawnAdapter` now retains each spawned child's `Popen` handle. Its `is_pid_alive` probes a tracked pid with `Popen.poll()` — `None` means still running, a returncode means the child has exited and is reaped on the spot, so it reports dead — and the dispatcher's real-mode reclaim sweep uses that method (the same adapter instance that spawned the children) as its liveness probe, so a dead worker is now detected and reclaimed within one tick instead of at TTL. A companion `reap_finished_children`, called once per tick after the sweep, reaps and drops the handles of children whose runs closed by any other path (the sweep never probes those pids), so no exited child lingers as a zombie and the handle map stays bounded. Untracked pids (e.g. a claim carried across a server restart) still fall back to the signal-0 `_pid_alive`.
2. **Instant-crash loops never trip the circuit breaker.** A dead-pid/expired reclaim closes the run `reclaimed`, which D6 deliberately exempts from `consecutive_failures` — so a hermes that dies instantly on every spawn (e.g. the missing-skill error) is reclaimed and respawned every tick indefinitely, with nothing to stop it.

Audit rounds 1–3 changed request-body parsing in the api.py files, not the spawn adapter (`src/planner/core/adapters/real.py`) or the reclaim sweep (`src/planner/dispatch/data.py`), which is why both findings survived them unchanged. Round 4 fixes finding #1 in the spawn adapter and the reclaim sweep's liveness probe (see above); finding #2 remains as a noted, spec-correct observation (§7.5/D6).

Cleanup: both re-run servers killed; no spawned hermes sessions left. The pre-existing `t09-smoke` `plan serve` (pid 43519, temp-directory DB, not started by the dogfood) remains for the lead to dispose of.

## Staleness rule

If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run. Evidence below always names the git commit of the tree it was produced on.
