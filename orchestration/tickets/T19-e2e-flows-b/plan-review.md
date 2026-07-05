# T19 plan review — codex output + orchestrator dispositions

Reviewer: `codex exec` (gpt-5.5, xhigh), full raw transcript in
`codex-plan-review-raw.txt` (185,886 tokens used). Prompt pointed it at plan.md,
ticket.md, SPEC §18.3 items 28–32, the T18 harness, and every source file the plan
cites, with instructions to verify claims against the code and report concrete
violations only. Two pre-dispositioned items were excluded from re-reporting (the
`server.py` shell gap and the claim-token approach — see "Orchestrator findings"
below, which codex was asked to stress-test instead).

## Codex findings

**F1 — [plan §5 e28/e29] `section[data-screen="day"]` matches nothing.**
`assets/app.js` creates every screen root as a `div` (`screenDiv`, app.js:59-62) and
`screens-day.js:23` sets `data-screen="day"` on that div; only Board and Ticket build
their own `<section>` (`screens-board.js:55`, `screens-ticket.js:427`). The Day ready
selector would never appear and e28/e29 would time out in `open_page`.

**Disposition: ACCEPTED** (independently verified against app.js:59-62 and the two
screens). Amendment A1: use content-bearing post-render ready selectors —
`.plan-tree [data-node="0"]` (e28), `.plan-tree [data-node="1"]` (e29). These only
exist once the day fetch + tree render landed, which is strictly stronger than the
attribute check (`data-screen="day"` is set before any fetch resolves and would pass
on an empty screen).

**F2 — [plan §4 e31] `_reload_settle` under-applies the settled-page discipline.**
The harness's `settled=True` path waits `flushes >= 1` and then re-anchors the ready
selector (conftest.py:186-193); the plan's `_reload_settle` only waits ready selector
+ `wsOpens >= 1`. After `page.reload()` the debug counters reset; without the flush
wait the "after" snapshot can be taken from the pre-catch-up render.

**Disposition: ACCEPTED.** Amendment A2: `_reload_settle` mirrors the full discipline
— reload, wait ready selector, wait `wsOpens >= 1`, wait `flushes >= 1`, re-anchor
ready selector; callers additionally re-wait their mid-flow selector before the
"after" snapshot (already in the plan).

No other violations reported. Codex's silence on the plan's remaining selector/
endpoint/report-shape claims is corroborated by my own independent spot-checks
(planTree/runHistory/reviewCard/board-marker selectors, `plan day add-ticket` /
`item create|set` / `ticket create --sprint` / `run close` argv, POST /api/sprints
body, GET /api/sprint/current shape, boundary tick four-key report and
replan-attempt literals, eligibility branches (a)/(b), `#/sprint` route registration).

## Orchestrator findings (pre-dispositioned, codex asked to stress-test)

**O1 — plan §0 finding 2 (claim env unobtainable) is WRONG; the claimless fallback is
rejected.** The claim token lives in `tickets.claim_lock` (dispatch/data.py:55) and
the harness hands every test its server's temp DB path (`ServerHandle.db_path`).
Reading it from the test's own fixture DB is in-fence and yields the exact token the
fake spawn adapter received. `require_claim` then validates it on both writes
(tickets/api.py propose_field when `ctx.is_claimed_agent`; dispatch/api.py close), the
`cli` fixture already plumbs `run_id=`/`claim=` to `PLAN_RUN_ID`/`PLAN_CLAIM`, and
cli/http.py maps them to `X-Plan-Run-Id`/`X-Plan-Claim`. So e30 runs SPEC-literally:
`plan propose result` WITH the claim env, then `plan run close --outcome done`.
Amendment A3 pins the exact steps. Codex reported no holes in this approach. The
resolution engine never consults the claim (`decide_file_proposal`), so the claimed
propose still auto-accepts to `needs_review`; `run_closed` is a logged event
(dispatch/data.py:206), so the post-close UI wait is flush-driven and safe. One
technical note folded into A3: the DB is WAL (core/db.py:152), so the test opens a
plain `sqlite3.connect(str(server.db_path))` (a `mode=ro` URI can fail on WAL when
the -shm isn't writable) for one SELECT and closes immediately.

**O2 — plan §0 finding 1 (shell misses `screens-sprint.js`) is REAL and out-of-fence.**
Escalated to the team lead (also affects T20's item 33, which needs
`screens-backlog.js` too); fix is two script tags in `src/planner/core/server.py`
`_SHELL`. e32 is written to the final selectors and passes once the shell loads the
script. Not a test-side deviation.
