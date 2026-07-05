# T21 plan review — codex findings + orchestrator dispositions

Raw codex session log: `codex-plan-review-raw.txt` (verdict: VIOLATIONS: 5).

Independent sense-check evidence: before dispositioning, the orchestrator live-ran the
plan's entire 12-step walkthrough by hand against a scratch demo-seeded test server
(port 8891, fake now 2026-07-04T12:00:00). Every pinned shape held exactly: R2 defaults
on create; park-then-supersede with `proposal_superseded` payload
`{field: success, replaced_body: ...}`; `next_ceiling: "none"` accept landing at
ceiling `needs_approach`/`propose`; recap write+overwrite; edit-accept storing the
trailing-space body byte-for-byte with event payload `edited: True`; plan accept →
`in_progress` with ceiling `needs_review`; day add/remove membership at
`day_2026-07-04`; pickup baseline with our P0-deadline-today ticket sorted FIRST;
block → absent, blocker `{"to":"done"}` jump → present; tick-dispatcher `spawned`
membership + `claim_active`; claim token read from SQLite; heartbeat; result propose
with claim env auto-accepting to `needs_review`; `run close --outcome done` clearing
the lease; approve → `done`. Additionally the day-plan decision (finding 1's fix) was
live-verified: `POST /api/day/<real-today>/plan/accept {"node": 1}` flips the demo
day's proposed child to `accepted` with no day-list change.

## Finding 1 — the 12-step table omits a day-plan decision. ACCEPTED (plan amended)

Item 35's explicit step list does not name a day-plan tree step, but both SPEC
§18.3(35) and ticket.md enumerate "day-plan decisions" among the human actions the
script performs through the HTTP API. The stricter reading costs one deterministic
step, so it is included. Fix: new step 9 — HTTP-human
`POST /api/day/<real-today>/plan/accept {"node": 1}` against the demo day (its plan
tree ships with child 1 `proposed`, `seed/demo.py:225-237`); assert
`plan.children[1].status == "accepted"` in the returned day view. The date is the
client-side real today — same host and tz as the server that seeded the demo seconds
earlier (`demo.py:30` uses the real clock in every context); the midnight race is
negligible and no fallback is added. Steps renumber to 13; the final trace line and
the test's asserted line become `DOGFOOD LEVEL A: 13/13 PASS`.

## Finding 2 — `ticket_json` vs `ticket_detail`. ACCEPTED (plan amended)

Correct: mutations return `ticket_json`; `GET /api/tickets/{id}` (and `plan ticket
show`) return `ticket_detail` — `ticket_json` plus `blocked`, `links`, `day_ids`,
`run_summary` (`tickets/views.py:121`, `tickets/api.py:258`). No assertion changes —
every asserted key exists in the shape actually returned (the plan already used the
detail-only `blocked` in the blocking step).

## Finding 3 — DOCS.md skills summary missing. ACCEPTED, ROUTED TO INTEGRATOR

SPEC §17 does require "DOCS.md summarizes their roles", but DOCS.md is not in T21's
owned file list and the memory files are maintained by the top-level integrator as
stages complete. Disposition: the implementer must NOT touch DOCS.md; the T21 report
carries an explicit hand-off note that the stage-7 integration must add the
four-skill summary to DOCS.md.

## Finding 4 — worker skill exit codes wrong. ACCEPTED (plan amended)

Per SPEC §8 / `cli/http.py:19,83,104`: 0 success, 1 validation/domain error (a
rejected write is one kind of exit-1, not its definition), 2 connection error. The
planning-worker outline says exactly that now.

## Finding 5 — planner-main routes day-plan decisions to the wrong screen. ACCEPTED (plan amended)

Day-plan accept/invalidate/accept-all/reject-all live on the Day screen (SPEC §10.1);
Review/Ticket handle proposal accepts and needs_review approval (§10.2, §10.4). The
planner-main outline's routing section is corrected accordingly.

All five findings are closed by the "Binding amendments (post-review)" section
appended to plan.md; no structural re-plan was needed.
