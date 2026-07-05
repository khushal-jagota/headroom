# DOGFOOD.md — the Section 18.5 fake-ticket dogfood record

Three escalating levels. Levels B and C are the one sanctioned exception to the fakes-only rule: they use the real local Hermes runtime, never inside `./verify`. Narrative without corresponding logs is not evidence; every claim below must point at an artifact (event-log excerpt, run row, file under `data/logs/`). Because `data/` is gitignored, durable copies of the load-bearing artifacts — the session and per-run logs, server-log tails, and full event-row dumps from both dogfood databases — are tracked in `orchestration/dogfood-evidence/`.

## Level A — the CLI as a user and agent would drive it (item 35, gating)

Status: **PASS, inside `./verify`.** `scripts/dogfood_cli.py` drives the full workflow against a demo-seeded test server — agent actions through the real CLI subprocess, human actions through the HTTP API — creating a ticket and taking it through every gate with every accept carrying its grant pair, including one edit-accept and one superseded proposal, recap written and overwritten, day assign/remove, a blocking link proven dispatch-ineligible then cleared, a claim via the test dispatcher tick, a result proposed with claim env, and the needs_review approval to done; each step's resulting state asserted from `--json`/response output, non-zero exit on first mismatch, step-by-step PASS trace printed. It runs as `test_e35_dogfood_level_a` in `tests/e2e/test_dogfood.py`. Evidence: the `[PASS] item 35 — dogfood Level A` scoreboard line in `orchestration/verify-runs/003-stage7-fix.md` (VERIFY: 36/36 PASS, exit 0) and in every subsequent verify run.

## Level B — a Hermes agent works a fake ticket

Status: **PASS — attempt 1** (2026-07-05, tree `f7db36776d4511c78cb07aa4e058e6e69478b924`, the final freeze). History, per the §18.5 staleness rule: passed at `7118677` (that session also ran `./verify` unprompted, edited PROGRESS.md, and deleted an untracked operator directory — origin of the guardrail sentence now standard in the prompt); passed again cleanly at `b52d836` after audit rounds 1–2; this run supersedes both after round 3 (`f7db367`) changed api.py request-body parsing, including the grant route used for shaping.

### What was run

1. Server (no test mode, dispatch off):
   ```
   PLAN_DB_PATH=data/dogfood-b3.db PLAN_PORT=8795 PLAN_DISPATCH_ENABLED=0 .venv/bin/plan serve
   ```
   Server log: `data/logs/level-b3-server.log` (tracked tail: `orchestration/dogfood-evidence/level-b-server-tail.log`).
2. Seed wrinkle (same in all three rounds, disclosed): on startup the real boundary job had already created a day row plus `day_created`/`day_closed`/`boundary_failed` events, so `plan seed --demo` refused with `db_not_empty`. As the human operator I cleared those three bootstrap tables (`DELETE FROM days; DELETE FROM events; DELETE FROM boundary_runs;` via sqlite3 on the scratch DB) and re-ran the seed, which then reported `{"sprints": 1, "sprint_items": 3, "tickets": 8, ...}`.
3. Ticket shaping (human action, grant route — exercising the round-3 body-shape change): the `needs_success` demo ticket `t_60ettg3z` "Draft the onboarding email success criteria." was raised via `POST /api/tickets/t_60ettg3z/grant {"ceiling":"needs_plan","at_cap":"propose"}` (event 23, `cause: human_grant`).
4. One real session, env pinned, all output captured — stock §18.5 prompt plus the standing guardrail sentence:
   ```
   PLAN_SERVER_URL=http://127.0.0.1:8795 PLAN_TICKET_ID=t_60ettg3z \
     hermes chat -q "Read /Users/khushaljagota/.hermes/planning-v2/skills/planning-worker.md, then work planning ticket t_60ettg3z using the plan CLI. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." \
     > data/logs/level-b3-attempt1.log 2>&1
   ```
   The session exited 0 after 1m44s. Session log: `data/logs/level-b3-attempt1.log` (102 lines; tracked copy: `orchestration/dogfood-evidence/level-b-attempt1.log`).

### Evidence (event log, `data/dogfood-b3.db`, also served by `GET /api/tickets/t_60ettg3z/events`; full event-row dump tracked at `orchestration/dogfood-evidence/level-b-events.txt` — 10 rows total on the ticket)

```
id  kind               payload (truncated)                                          local time (2026-07-05)
8   ticket_created     {"title": "Draft the onboarding email success criteria." …}  19:58:56
23  grant_changed      {"ceiling": "needs_plan", "at_cap": "propose", "cause": "human_grant"}  19:59:08
25  proposal_accepted  {"field": "success", "body": "Success means we have a concise, usable definition of w…"}  20:00:27
26  state_changed      {"from": "needs_success", "to": "needs_approach", "cause": "auto_accept"}  20:00:27
27  recap_updated      {}                                                            20:00:34
28  proposal_accepted  {"field": "approach", "body": "Approach:\n\n1. Treat the onboarding email as a behavi…"}  20:00:39
29  state_changed      {"from": "needs_approach", "to": "needs_plan", "cause": "auto_accept"}  20:00:39
30  recap_updated      {}                                                            20:00:43
31  proposal_filed     {"field": "plan", "body": "Plan proposal:\n\n1. Confirm assumptions before copywritin…", "proposed_by": "agent"}  20:00:54
32  recap_updated      {}                                                            20:00:59
```

Proposal writes by the session: 3 (events 25, 28 accepted; 31 filed — carrying `"proposed_by": "agent"`, the §7.6 non-dispatched attribution). Recap writes: 3 (events 27, 30, 32). Requirement was ≥1 of each. Final ticket state (verified in the DB after the session): `needs_plan` / ceiling `needs_plan` / at_cap `propose` — success and approach accepted below the ceiling, the plan proposal parked at the ceiling for human review, exactly as `at_cap = propose` dictates.

### Attempts log (three-attempt rule)

- **Attempt 1 — PASS.** Stock prompt shape plus guardrail. The session found `.venv/bin/plan`, oriented with `ticket show --json`, and worked the ticket to its ceiling in 1m44s. No further attempts needed.

### Conduct

The session stayed inside its brief: its closing message states "No ./verify run. No repository files modified or deleted." and `git status` after the session showed a clean working tree. Same conduct as the `b52d836` round; the guardrail sentence has held in every guardrailed run.

## Level C — a dispatched ticket agent end to end

Status: **PASS — wrapper shim path, single run** (2026-07-05, tree `f7db36776d4511c78cb07aa4e058e6e69478b924`, the final freeze). History, per the §18.5 staleness rule: passed at `7118677` (attempt 1 there: the stock spawn command died on `Error: Unknown skill(s): planning-worker` — the skill is deliberately not installed in `~/.hermes` — the run closing `reclaimed`/`dead_pid`; attempt 2 passed through the shim, two runs); passed again at `b52d836` (shim, two runs). The stock-command failure mode is environmental and unchanged by the audit rounds (which touched api.py request-body parsing, not the spawn adapter), so this round went straight to the shim per the lead's instruction; the stock failure remains honestly recorded from the first round.

### Setup

Two-phase start so the dispatcher could not claim mid-shaping: first `PLAN_DB_PATH=data/dogfood-c3.db PLAN_PORT=8796 PLAN_DISPATCH_ENABLED=0 .venv/bin/plan serve` (shaping log: `data/logs/level-c3-shaping.log`), seed `--demo` (same `db_not_empty` bootstrap-row wrinkle as Level B, cleared the same way), then shaping via the grant route (human actions):

- Target `t_cr043k8y` "Draft the onboarding email success criteria." (state `needs_success`) granted ceiling `in_progress`, at_cap `propose`.
- The three other dispatch-eligible demo tickets stop-capped at their current states: `t_f248u5ps` → (`in_progress`, `stop`), `t_z42zauaz` → (`needs_approach`, `stop`), `t_b9nva0cx` → (`needs_plan`, `stop`). The rest were already ineligible by state.
- Verified with `plan queue pickup --json`: exactly one entry, `t_cr043k8y`.

Then the shaping server was killed and the same DB relaunched with real dispatch through the disclosed shim: `PLAN_DB_PATH=data/dogfood-c3.db PLAN_PORT=8796 PLAN_DISPATCH_ENABLED=1 PLAN_TICK_SECONDS=20 PLAN_MAX_RUNS=1 PLAN_HERMES_BIN=orchestration/dogfood/hermes-wrapped.sh .venv/bin/plan serve` (log: `data/logs/level-c3-server.log`; tracked tail: `orchestration/dogfood-evidence/level-c-server-tail.log`). No test mode; `spawn_adapter: auto` resolved to the real adapter. The shim (`orchestration/dogfood/hermes-wrapped.sh`, sanctioned §18.5 "prompt shape" latitude) execs the real hermes with the unresolvable `--skills planning-worker` flag dropped and the message enriched to "Read <repo>/skills/planning-worker.md, then work planning ticket <id>. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." Nothing in `src/` changed; the shim only rewrites the spawn command line.

### Evidence (runs table + event log, `data/dogfood-c3.db`; full event-row + run-row dump tracked at `orchestration/dogfood-evidence/level-c-events.txt` — 17 rows total on the ticket, 15 of them the dispatch chain)

Run (`SELECT id,ticket_id,status,started_at,ended_at,pid FROM runs` — exactly one row):

```
run_eenhta99  t_cr043k8y  done  19:59:59 → 20:02:17  pid 87736  summary: "Worked ticket t_cr043k8y to its in_progress ceiling. Success, approach, and plan were accepted; result proposal is parked with rev…"
```

Event sequence on `t_cr043k8y` (all times 2026-07-05 local; the dispatcher claimed on its first tick):

```
28  run_started       {"run_id": "run_eenhta99"}                                    19:59:59
29  claim_heartbeat   {"run_id": "run_eenhta99", "claim_expires": 1783278955}       20:00:55
30  proposal_accepted {"field": "success", "body": "Success means the ticket has a concrete, reviewable set…"}  20:01:12
31  state_changed     needs_success → needs_approach (auto_accept)                  20:01:12
32  recap_updated                                                                   20:01:17
33  proposal_accepted {"field": "approach", "body": "Approach: treat this as a content strategy artifact, n…"}  20:01:23
34  state_changed     needs_approach → needs_plan (auto_accept)                     20:01:23
35  recap_updated                                                                   20:01:27
36  proposal_accepted {"field": "plan", "body": "1. State the email's single job in one sentence.\n2. Defin…"}  20:01:35
37  state_changed     needs_plan → in_progress (auto_accept) — the ceiling          20:01:35
38  recap_updated                                                                   20:01:44
39  proposal_filed    {"field": "result", …, "proposed_by": "agent"}                20:02:03   — parks at the ceiling
40  note_updated      {"field": "result"}                                           20:02:11
41  recap_updated                                                                   20:02:12
42  run_closed        {"run_id": "run_eenhta99", "status": "done", …}               20:02:17
```

Chain row counts: 1 run_started, 1 claim_heartbeat, 3 proposal_accepted, 3 state_changed, 1 proposal_filed, 1 note_updated, 4 recap_updated, 1 run_closed. Every write was made under the active claim (server-side §7.6 validation, through the audit rounds' authctx claim validation, would otherwise have rejected it; the heartbeat at event 29 names the run id).

Difference from the two prior rounds, in the system's favor: this worker filed the parked `result` proposal (and a reviewer note on the field — the skill's notes discipline) inside the same run before closing, so the ticket left the run already ineligible (pending proposal on the gating field) and **no second dispatch was needed** — one run, the complete claim → heartbeat → propose ×4 → note → recap → close chain. Confirmed: `plan queue pickup --json` returned `{"pickup": []}` and the runs count was still 1 at 20:26:31 — 24 minutes and roughly 72 ticks after the run closed. The prior rounds' two-run shape (first run closing at the ceiling without proposing `result`, next tick correctly re-claiming) is equally valid behavior; both terminate by design, not by luck.

Final ticket state (`GET /api/tickets/t_cr043k8y`): state `in_progress` = ceiling, `at_cap propose`, success/approach/plan accepted, result proposal pending for human review with a result-field note set, `auto_blocked false`, `consecutive_failures 0`. The ticket landed exactly where its ceiling dictates.

Per-run log under `data/logs/` (tracked copy in `orchestration/dogfood-evidence/`): `run_eenhta99.log` (7.5 KB). The session respected the guardrail; the working tree was clean after the entire re-run.

### Systemic findings (from the `7118677` round, still present at `f7db367` — worth tickets)

1. **Dead spawn children are invisible to the pid sweep until claim-TTL expiry.** The server never reaps its spawned Popen children, so a crashed hermes becomes a zombie, and `_pid_alive`'s `os.kill(pid, 0)` reports zombies alive — the dead-pid reclaim cannot fire while the spawning server lives; the run sits `running` up to 15 minutes. (Observed directly at `7118677`; the reclaim then fired correctly only because a server restart let the OS reap the zombie.)
2. **Instant-crash loops never trip the circuit breaker.** A dead-pid/expired reclaim closes the run `reclaimed`, which D6 deliberately exempts from `consecutive_failures` — so a hermes that dies instantly on every spawn (e.g. the missing-skill error) is reclaimed and respawned every tick indefinitely, with nothing to stop it.

The audit rounds changed request-body parsing in the api.py files, not the spawn adapter (`src/planner/core/adapters/real.py`) or the reclaim sweep (`src/planner/dispatch/data.py`), so both findings carry over unchanged.

Cleanup: both re-run servers killed; no spawned hermes sessions left. The pre-existing `t09-smoke` `plan serve` (pid 43519, temp-directory DB, not started by the dogfood) remains for the lead to dispose of.

## Staleness rule

If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run. Evidence below always names the git commit of the tree it was produced on.
