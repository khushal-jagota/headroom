# DOGFOOD.md — the Section 18.5 fake-ticket dogfood record

Three escalating levels. Levels B and C are the one sanctioned exception to the fakes-only rule: they use the real local Hermes runtime, never inside `./verify`. Narrative without corresponding logs is not evidence; every claim below must point at an artifact (event-log excerpt, run row, file under `data/logs/`). Because `data/` is gitignored, durable copies of the load-bearing artifacts — the session and per-run logs, server-log tails, and full event-row dumps from both dogfood databases — are tracked in `orchestration/dogfood-evidence/`.

## Level A — the CLI as a user and agent would drive it (item 35, gating)

Status: **PASS, inside `./verify`.** `scripts/dogfood_cli.py` drives the full workflow against a demo-seeded test server — agent actions through the real CLI subprocess, human actions through the HTTP API — creating a ticket and taking it through every gate with every accept carrying its grant pair, including one edit-accept and one superseded proposal, recap written and overwritten, day assign/remove, a blocking link proven dispatch-ineligible then cleared, a claim via the test dispatcher tick, a result proposed with claim env, and the needs_review approval to done; each step's resulting state asserted from `--json`/response output, non-zero exit on first mismatch, step-by-step PASS trace printed. It runs as `test_e35_dogfood_level_a` in `tests/e2e/test_dogfood.py`. Evidence: the `[PASS] item 35 — dogfood Level A` scoreboard line in `orchestration/verify-runs/003-stage7-fix.md` (VERIFY: 36/36 PASS, exit 0) and in every subsequent verify run.

## Level B — a Hermes agent works a fake ticket

Status: **PASS — attempt 1 of the re-run** (2026-07-05, tree `b52d836ee7ff54217ecda64f41fa7c7f8e0b077c`). History: previously passed at `7118677` with equivalent evidence (that first run's session also ran `./verify` unprompted, edited PROGRESS.md, and deleted an untracked operator directory — hence the guardrail sentence now in the prompt); re-run required by the §18.5 staleness rule after the audit-round server fixes (`d336e66`/`c4b0467`).

### What was run

1. Server (no test mode, dispatch off):
   ```
   PLAN_DB_PATH=data/dogfood-b2.db PLAN_PORT=8793 PLAN_DISPATCH_ENABLED=0 .venv/bin/plan serve
   ```
   Server log: `data/logs/level-b2-server.log` (tracked tail: `orchestration/dogfood-evidence/level-b-server-tail.log`).
2. Seed wrinkle (same as the first run, disclosed): on startup the real boundary job had already created a day row plus `day_created`/`day_closed`/`boundary_failed` events, so `plan seed --demo` refused with `db_not_empty`. As the human operator I cleared those three bootstrap tables (`DELETE FROM days; DELETE FROM events; DELETE FROM boundary_runs;` via sqlite3 on the scratch DB) and re-ran the seed, which then reported `{"sprints": 1, "sprint_items": 3, "tickets": 8, ...}`.
3. Ticket shaping (human action, grant route): no demo ticket sits at `needs_success` with ceiling `needs_plan`, so the closest — `t_pqpfu1c7` "Draft the onboarding email success criteria." (state `needs_success`, ceiling `needs_success`, at_cap `propose`) — was raised via `POST /api/tickets/t_pqpfu1c7/grant {"ceiling":"needs_plan","at_cap":"propose"}` (event 23, `cause: human_grant`).
4. One real session, env pinned, all output captured — stock §18.5 prompt plus the standing guardrail sentence:
   ```
   PLAN_SERVER_URL=http://127.0.0.1:8793 PLAN_TICKET_ID=t_pqpfu1c7 \
     hermes chat -q "Read /Users/khushaljagota/.hermes/planning-v2/skills/planning-worker.md, then work planning ticket t_pqpfu1c7 using the plan CLI. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." \
     > data/logs/level-b2-attempt1.log 2>&1
   ```
   The session exited 0 after 1m58s. Session log: `data/logs/level-b2-attempt1.log` (41 lines; tracked copy: `orchestration/dogfood-evidence/level-b-attempt1.log`).

### Evidence (event log, `data/dogfood-b2.db`, also served by `GET /api/tickets/t_pqpfu1c7/events`; full event-row dump tracked at `orchestration/dogfood-evidence/level-b-events.txt`)

```
id  kind               payload (truncated)                                          local time (2026-07-05)
8   ticket_created     {"title": "Draft the onboarding email success criteria." …}  19:07:54
23  grant_changed      {"ceiling": "needs_plan", "at_cap": "propose", "cause": "human_grant"}  19:08:18
25  proposal_accepted  {"field": "success", "body": "Success means the onboarding email has clear acceptance crit…"}  19:09:42
26  state_changed      {"from": "needs_success", "to": "needs_approach", "cause": "auto_accept"}  19:09:42
27  recap_updated      {}                                                            19:09:47
28  proposal_accepted  {"field": "approach", "body": "Approach:\n\n1. Treat the email as an activation asset, not…"}  19:09:52
29  state_changed      {"from": "needs_approach", "to": "needs_plan", "cause": "auto_accept"}  19:09:52
30  recap_updated      {}                                                            19:09:57
31  proposal_filed     {"field": "plan", "body": "Plan:\n\n1. Confirm the email's working context before drafting…", "proposed_by": "agent"}  19:10:07
32  recap_updated      {}                                                            19:10:16
```

Proposal writes by the session: 3 (events 25, 28, 31 — event 31 carries `"proposed_by": "agent"`, the §7.6 non-dispatched attribution). Recap writes: 3 (events 27, 30, 32). Requirement was ≥1 of each. Final ticket state (verified in the DB after the session): `needs_plan` / ceiling `needs_plan` / at_cap `propose` — success and approach accepted below the ceiling, the plan proposal parked at the ceiling for human review, exactly as `at_cap = propose` dictates.

### Attempts log (three-attempt rule)

- **Attempt 1 — PASS.** Stock prompt shape plus guardrail. The session noted `plan` was not on PATH, used `./.venv/bin/plan`, oriented with `ticket show --json`, and worked the ticket to its ceiling. No further attempts needed.

### Conduct

Unlike the first run at `7118677`, this session stayed inside its brief: its closing message states "I did not run ./verify and did not modify/delete repository files", and `git status` after the session shows a clean working tree. The guardrail sentence is doing its job.

## Level C — a dispatched ticket agent end to end

Status: **PASS — re-run, wrapper shim path** (2026-07-05, tree `b52d836ee7ff54217ecda64f41fa7c7f8e0b077c`). History: previously passed at `7118677` with equivalent evidence (attempt 1 there: the stock spawn command died on `Error: Unknown skill(s): planning-worker` because the skill is deliberately not installed in `~/.hermes`, the run closing `reclaimed`/`dead_pid`; attempt 2 passed through the wrapper shim); re-run required by the §18.5 staleness rule after the audit-round server fixes (`d336e66`/`c4b0467`). Attempt 1's failure mode is environmental (missing skill in `~/.hermes`), untouched by those fixes, so the re-run went straight to the shim path per the lead's instruction; the stock-command failure remains honestly recorded from the first round.

### Setup

Two-phase start so the dispatcher could not claim mid-shaping: first `PLAN_DB_PATH=data/dogfood-c2.db PLAN_PORT=8794 PLAN_DISPATCH_ENABLED=0 .venv/bin/plan serve` (shaping log: `data/logs/level-c2-shaping.log`), seed `--demo` (same `db_not_empty` bootstrap-row wrinkle as Level B, cleared the same way), then shaping via the grant route (human actions):

- Target `t_nb3m0ta8` "Draft the onboarding email success criteria." (state `needs_success`) granted ceiling `in_progress`, at_cap `propose`.
- The three other dispatch-eligible demo tickets stop-capped at their current states: `t_nardwhnj` → (`in_progress`, `stop`), `t_ex26nsw4` → (`needs_approach`, `stop`), `t_8tvx5upf` → (`needs_plan`, `stop`). The rest were already ineligible by state.
- Verified with `plan queue pickup --json`: exactly one entry, `t_nb3m0ta8`.

Then the shaping server was killed and the same DB relaunched with real dispatch through the disclosed shim: `PLAN_DB_PATH=data/dogfood-c2.db PLAN_PORT=8794 PLAN_DISPATCH_ENABLED=1 PLAN_TICK_SECONDS=20 PLAN_MAX_RUNS=1 PLAN_HERMES_BIN=orchestration/dogfood/hermes-wrapped.sh .venv/bin/plan serve` (log: `data/logs/level-c2-server.log`; tracked tail: `orchestration/dogfood-evidence/level-c-server-tail.log`). No test mode; `spawn_adapter: auto` resolved to the real adapter. The shim (`orchestration/dogfood/hermes-wrapped.sh`, sanctioned §18.5 "prompt shape" latitude) execs the real hermes with the unresolvable `--skills planning-worker` flag dropped and the message enriched to "Read <repo>/skills/planning-worker.md, then work planning ticket <id>. Act only through the plan CLI; do not run ./verify, do not modify or delete any repository files." Nothing in `src/` changed; the shim only rewrites the spawn command line.

### Evidence (runs table + event log, `data/dogfood-c2.db`; full event-row + run-row dump tracked at `orchestration/dogfood-evidence/level-c-events.txt`)

Runs (`SELECT id,ticket_id,status,started_at,ended_at,pid FROM runs`):

```
run_egsw8wzs  t_nb3m0ta8  done  19:09:12 → 19:10:47  pid 66933  summary: "Worked ticket to its ceiling. Success, approach, and plan were accepted; ticket is now in_progress with execution steps …"
run_wv0pm4uf  t_nb3m0ta8  done  19:10:52 → 19:11:55  pid 67971  summary: "Done: proposed the result for t_nb3m0ta8 at the in_progress ceiling. The result contains 7 testable onboarding email suc…"
```

Event sequence on `t_nb3m0ta8` (all times 2026-07-05 local):

```
28  run_started       {"run_id": "run_egsw8wzs"}                                    19:09:12
29  claim_heartbeat   {"run_id": "run_egsw8wzs", "claim_expires": 1783275885}       19:09:45
30  proposal_accepted {"field": "success", "body": "Success means the onboarding email has clear, reviewer-ready succ…"}  19:10:09
31  state_changed     needs_success → needs_approach (auto_accept)                  19:10:09
32  recap_updated                                                                   19:10:13
33  proposal_accepted {"field": "approach", "body": "Approach: treat this as a positioning and review-standard exerci…"}  19:10:23
34  state_changed     needs_approach → needs_plan (auto_accept)                     19:10:23
35  recap_updated                                                                   19:10:27
36  proposal_accepted {"field": "plan", "body": "1. Identify the exact onboarding email moment: who receives it, what…"}  19:10:36
37  state_changed     needs_plan → in_progress (auto_accept) — the ceiling          19:10:36
38  recap_updated                                                                   19:10:40
39  run_closed        {"run_id": "run_egsw8wzs", "status": "done", …}               19:10:47
40  run_started       {"run_id": "run_wv0pm4uf"}                                    19:10:52
41  claim_heartbeat   {"run_id": "run_wv0pm4uf", "claim_expires": 1783275991}       19:11:31
42  proposal_filed    {"field": "result", …, "proposed_by": "agent"}                19:11:47   — parks at the ceiling
43  recap_updated                                                                   19:11:51
44  run_closed        {"run_id": "run_wv0pm4uf", "status": "done", …}               19:11:55
```

Every write in both runs was made under an active claim (server-side §7.6 validation — now via the audit round's authctx claim validation — would otherwise have rejected it; the heartbeats at events 29/41 name the run ids). Second-run note, same shape as the first round: after run 1 closed `done`, the ticket was *still eligible* (at ceiling, `at_cap = propose`, no pending proposal on the gating field `result`), so the next tick correctly claimed it again; run 2 filed the result proposal, which parks — after which `plan queue pickup --json` returned `{"pickup": []}` and the run count stayed at 2 across nearly three further ticks (checked at 19:12:51). The loop terminates by design, not by luck.

Final ticket state (`GET /api/tickets/t_nb3m0ta8`): state `in_progress` = ceiling, `at_cap propose`, success/approach/plan accepted, result proposal pending for human review, `auto_blocked false`, `consecutive_failures 0`. The ticket landed exactly where its ceiling dictates.

Per-run logs under `data/logs/` (tracked copies in `orchestration/dogfood-evidence/`): `run_egsw8wzs.log` (8.7 KB), `run_wv0pm4uf.log` (3.4 KB). Both sessions respected the guardrail; the working tree was clean after the entire re-run.

Cleanup: both re-run servers killed; no spawned hermes sessions left. The pre-existing `t09-smoke` `plan serve` (pid 43519, temp-directory DB, not started by the dogfood) remains for the lead to dispose of.

## Staleness rule

If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run. Evidence below always names the git commit of the tree it was produced on.
