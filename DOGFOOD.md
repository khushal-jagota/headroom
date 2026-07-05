# DOGFOOD.md — the Section 18.5 fake-ticket dogfood record

Three escalating levels. Levels B and C are the one sanctioned exception to the fakes-only rule: they use the real local Hermes runtime, never inside `./verify`. Narrative without corresponding logs is not evidence; every claim below must point at an artifact (event-log excerpt, run row, file under `data/logs/`).

## Level A — the CLI as a user and agent would drive it (item 35, gating)

Status: **PASS, inside `./verify`.** `scripts/dogfood_cli.py` drives the full workflow against a demo-seeded test server — agent actions through the real CLI subprocess, human actions through the HTTP API — creating a ticket and taking it through every gate with every accept carrying its grant pair, including one edit-accept and one superseded proposal, recap written and overwritten, day assign/remove, a blocking link proven dispatch-ineligible then cleared, a claim via the test dispatcher tick, a result proposed with claim env, and the needs_review approval to done; each step's resulting state asserted from `--json`/response output, non-zero exit on first mismatch, step-by-step PASS trace printed. It runs as `test_e35_dogfood_level_a` in `tests/e2e/test_dogfood.py`. Evidence: the `[PASS] item 35 — dogfood Level A` scoreboard line in `orchestration/verify-runs/003-stage7-fix.md` (VERIFY: 36/36 PASS, exit 0) and in every subsequent verify run.

## Level B — a Hermes agent works a fake ticket

Status: **PASS — attempt 1** (2026-07-05, tree `7118677696e46c1879cd7f30ecebe688b443911c`).

### What was run

1. Server (no test mode, dispatch off):
   ```
   PLAN_DB_PATH=data/dogfood-b.db PLAN_PORT=8791 PLAN_DISPATCH_ENABLED=0 .venv/bin/plan serve
   ```
   Server log: `data/logs/level-b-server.log`.
2. Seed wrinkle, disclosed: on startup the real boundary job had already created a day row plus `day_created`/`day_closed`/`boundary_failed` events, so `plan seed --demo` refused with `db_not_empty`. As the human operator I cleared those three bootstrap tables (`DELETE FROM days; DELETE FROM events; DELETE FROM boundary_runs;` via sqlite3 on the scratch DB) and re-ran the seed, which then reported `{"sprints": 1, "sprint_items": 3, "tickets": 8, ...}`.
3. Ticket shaping (human action, grant route): no demo ticket sits at `needs_success` with ceiling `needs_plan`, so the closest — `t_gfsfcm7z` "Draft the onboarding email success criteria." (state `needs_success`, ceiling `needs_success`, at_cap `propose`) — was raised via `POST /api/tickets/t_gfsfcm7z/grant {"ceiling":"needs_plan","at_cap":"propose"}` (event 24, `cause: human_grant`).
4. One real session, env pinned, all output captured:
   ```
   PLAN_SERVER_URL=http://127.0.0.1:8791 PLAN_TICKET_ID=t_gfsfcm7z \
     hermes chat -q "Read /Users/khushaljagota/.hermes/planning-v2/skills/planning-worker.md, then work planning ticket t_gfsfcm7z using the plan CLI" \
     > data/logs/level-b-attempt1.log 2>&1
   ```
   The session exited 0 after ~4 minutes. Session log: `data/logs/level-b-attempt1.log` (154 lines).

### Evidence (event log, `data/dogfood-b.db`, also served by `GET /api/tickets/t_gfsfcm7z/events`)

```
id  kind               payload (truncated)                                          local time
8   ticket_created     {"title": "Draft the onboarding email success criteria." …}  2026-07-05 15:53:21
24  grant_changed      {"ceiling": "needs_plan", "at_cap": "propose", "cause": "human_grant"}  15:53:44
25  proposal_accepted  {"field": "success", "body": "Success means there is a short, reviewable …"}  15:55:21
26  state_changed      {"from": "needs_success", "to": "needs_approach", "cause": "auto_accept"}  15:55:21
27  recap_updated      {}                                                            15:55:21
28  proposal_accepted  {"field": "approach", "body": "Approach: define the success criteria as a practical writing brief …"}  15:55:21
29  state_changed      {"from": "needs_approach", "to": "needs_plan", "cause": "auto_accept"}  15:55:21
30  recap_updated      {}                                                            15:55:21
31  proposal_filed     {"field": "plan", "body": "Plan:\n\n1. Draft the success-criteria brief in five sections …", "proposed_by": "agent"}  15:55:21
32  recap_updated      {}                                                            15:55:22
```

Proposal writes by the session: 3 (events 25, 28, 31 — event 31 carries `"proposed_by": "agent"`, the §7.6 non-dispatched attribution). Recap writes: 3 (events 27, 30, 32). Requirement was ≥1 of each. Final ticket state: `needs_plan` — success and approach accepted below the ceiling, the plan proposal parked at the ceiling for human review, exactly as `at_cap = propose` dictates. Final recap (from `GET /api/tickets/t_gfsfcm7z`): "Worked to ceiling needs_plan. Success and approach are accepted. Plan proposal is parked for human review …".

### Attempts log (three-attempt rule)

- **Attempt 1 — PASS.** Prompt shape exactly as specified in §18.5. The session found `.venv/bin/plan` on its own, oriented with `plan ticket show --json`, and worked the ticket to its ceiling. No further attempts needed.

### Side effects, disclosed honestly

The spawned agent went beyond its ticket brief (all visible in `data/logs/level-b-attempt1.log`):

- It ran `./verify` on its own initiative (reported `VERIFY: 36/36 PASS`). The dogfood operator did not run it.
- It appended a one-line dogfood note to `PROGRESS.md` (left in place, uncommitted, for the lead to keep or drop).
- It ran `rm -rf orchestration/dogfood` — deleting the operator's untracked Level C wrapper script, which was recreated afterwards. No tracked files were deleted.
- It correctly noted it had no `PLAN_RUN_ID`/`PLAN_CLAIM` (expected at Level B — no dispatcher run exists to heartbeat or close).

## Level C — a dispatched ticket agent end to end

Status: **PASS — attempt 2** (2026-07-05, tree `7118677696e46c1879cd7f30ecebe688b443911c`; attempt 1 recorded below as a legitimate failure).

### Setup (common to both attempts)

Two-phase start so the dispatcher could not claim mid-shaping: first `PLAN_DB_PATH=data/dogfood-c.db PLAN_PORT=8792 PLAN_DISPATCH_ENABLED=0 .venv/bin/plan serve` (shaping log: `data/logs/level-c-shaping.log`), seed `--demo` (same `db_not_empty` bootstrap-row wrinkle as Level B, cleared the same way), then shaping via the grant route (human actions):

- Target `t_cf8z7aq0` "Draft the onboarding email success criteria." (state `needs_success`) granted ceiling `in_progress`, at_cap `propose`.
- The three other dispatch-eligible demo tickets stop-capped at their current states: `t_tvtm5jnk` → (`in_progress`, `stop`), `t_vxh0bqmx` → (`needs_approach`, `stop`), `t_w05bp2fm` → (`needs_plan`, `stop`). The rest were already ineligible by state.
- Verified with `plan queue pickup --json`: exactly one entry, `t_cf8z7aq0`.

Then the shaping server was killed and the same DB relaunched with real dispatch: `PLAN_DB_PATH=data/dogfood-c.db PLAN_PORT=8792 PLAN_DISPATCH_ENABLED=1 PLAN_TICK_SECONDS=20 PLAN_MAX_RUNS=1 .venv/bin/plan serve` (log: `data/logs/level-c-server.log`). No test mode; `spawn_adapter: auto` resolved to the real adapter.

### Attempts log (three-attempt rule)

- **Attempt 1 — FAIL (recorded honestly).** Stock adapter command. First tick claimed the ticket: run `run_49eek207`, pid 4189, per-run log `data/logs/run_49eek207.log`. The spawned session died instantly: `Error: Unknown skill(s): planning-worker` — the skill is deliberately not installed in `~/.hermes` (§18.5). Observation for the record: the dead child became a **zombie** (the server holds the Popen without reaping), and `os.kill(pid, 0)` reports zombies alive, so the dead-pid sweep did not fire; the run sat `running` and would only have been reclaimed at claim-TTL expiry (15 min). No proposals, no recap. Run outcome: closed `reclaimed` (reason `dead_pid`, event 29) by the attempt-2 server's first tick after the restart reaped the zombie — `reclaimed` by design leaves the circuit breaker unchanged (D6), so `consecutive_failures` stayed 0.
- **Attempt 2 — PASS.** Materially different prompt shape, sanctioned by §18.5: the server relaunched with `PLAN_HERMES_BIN=orchestration/dogfood/hermes-wrapped.sh` — a disclosed repo-local shim that execs the real hermes with the unresolvable `--skills planning-worker` flag dropped and the message enriched to "Read <repo>/skills/planning-worker.md, then work planning ticket t_cf8z7aq0. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." (guardrail added after the Level B session's repo side effects). Nothing in `src/` changed; the shim only rewrites the spawn command line.

### Evidence (runs table + event log, `data/dogfood-c.db`)

Runs (`SELECT id,ticket_id,status,started_at,ended_at,pid FROM runs`):

```
run_49eek207  t_cf8z7aq0  reclaimed  16:01:46 → 16:05:31  pid 4189   (attempt 1, dead skill error)
run_2wvs10j3  t_cf8z7aq0  done       16:05:31 → 16:07:46  pid 6247   summary: "Drove t_cf8z7aq0 to its ceiling. Success, approach, and plan were proposed and auto-accepted; ticket is now in_progress with a recap for the next worker."
run_61x513rd  t_cf8z7aq0  done       16:07:51 → 16:08:39  pid 7643   summary: "Result proposal filed for t_cf8z7aq0 at the …"
```

Event sequence on `t_cf8z7aq0` (all times 2026-07-05 local):

```
28  run_started      {"run_id": "run_49eek207"}                                     16:01:46
29  claim_reclaimed  {"run_id": "run_49eek207", "reason": "dead_pid"}               16:05:31
30  run_started      {"run_id": "run_2wvs10j3"}                                     16:05:31
31  claim_heartbeat  {"run_id": "run_2wvs10j3", "claim_expires": 1783264920}        16:07:00
32  proposal_accepted {"field": "success", …, "resolved_by": "auto"}                16:07:16
33  state_changed    needs_success → needs_approach (auto_accept)                   16:07:16
34  recap_updated                                                                   16:07:21
35  proposal_accepted {"field": "approach", …}                                      16:07:26
36  state_changed    needs_approach → needs_plan (auto_accept)                      16:07:26
37  recap_updated                                                                   16:07:30
38  proposal_accepted {"field": "plan", …}                                          16:07:36
39  state_changed    needs_plan → in_progress (auto_accept) — the ceiling           16:07:36
40  recap_updated                                                                   16:07:42
41  run_closed       {"run_id": "run_2wvs10j3", "status": "done", …}                16:07:46
42  run_started      {"run_id": "run_61x513rd"}                                     16:07:51
43  claim_heartbeat  {"run_id": "run_61x513rd", "claim_expires": 1783265015}        16:08:35
44  proposal_filed   {"field": "result", …, "proposed_by": "agent"}                 16:08:35   — parks at the ceiling
45  recap_updated                                                                   16:08:39
46  run_closed       {"run_id": "run_61x513rd", "status": "done", …}                16:08:39
```

Every write in runs 2 and 3 was made under an active claim (server-side §7.6 validation would otherwise have rejected it; the heartbeats at events 31/43 name the run ids). Third-run note: after run 2 closed `done`, the ticket was *still eligible* (at ceiling, `at_cap = propose`, no pending proposal on the gating field `result`), so the next tick correctly claimed it again; run 3 filed the result proposal, which parks — after which `plan queue pickup --json` returned `{"pickup": []}` and no further run spawned across two additional ticks. The loop terminates by design, not by luck.

Final ticket state (`GET /api/tickets/t_cf8z7aq0`): state `in_progress` = ceiling, `at_cap propose`, success/approach/plan accepted, result proposal pending for human review, `auto_blocked false`, `consecutive_failures 0`. The ticket landed exactly where its ceiling dictates.

Per-run logs under `data/logs/`: `run_49eek207.log` (78 bytes, the skill error), `run_2wvs10j3.log` (11 KB), `run_61x513rd.log` (4.6 KB — its final message: "I only used the plan CLI for ticket actions. No repository files modified or deleted. ./verify not run.").

Cleanup: both dogfood servers killed; `pgrep -f "planning ticket"` empty. One pre-existing `plan serve` (pid 43519, a `t09-smoke` server on a temp-directory DB, not started by this dogfood run) was left untouched and reported to the lead.

## Staleness rule

If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run. Evidence below always names the git commit of the tree it was produced on.
