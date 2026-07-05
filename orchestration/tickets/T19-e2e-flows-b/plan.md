# T19 — implementation blueprint for `tests/e2e/test_flows_b.py`

SPEC §18.3 items 28–32. Playwright/chromium e2e on the T18 harness. The implementer
follows this verbatim; every selector, argv, body, wait, and literal is pinned below
and mapped to its SPEC line + source guarantee.

---

## 0. Files & required companion changes

**File to write:** `tests/e2e/test_flows_b.py` (the ONLY deliverable).

**No conftest change is needed.** Every fixture used (`server`, `context_factory`,
`open_page`, `cli`, `api`) already exists and is sufficient. The `/api/test/*`
endpoints are driven through `api.human_post` (header-less POST); no new helper
fixture is required.

**Two findings that block items as literally specified — FLAGGED LOUDLY, both OUTSIDE
the test fence. Neither can be fixed inside `test_flows_b.py`; the integrator/orchestrator
must decide:**

1. **Item 32 (Sprint) is blocked by a missing `<script>` tag.** The app shell
   `_SHELL` in `src/planner/core/server.py` (lines 57–66) loads `screens-day.js`,
   `screens-review.js`, `screens-board.js`, `screens-ticket.js` — but **not**
   `screens-sprint.js` (which exists and is functional in `assets/`). Navigating to
   `#/sprint` therefore renders app.js's placeholder `"not built yet"`
   (`assets/app.js:28-32`), so the ready selector `[data-status-group="active"]`
   never appears and item 32 hard-fails at `open_page`.
   **Required one-line edit (server.py, after line 65):**
   ```
   <script src="/assets/screens-ticket.js"></script>
   <script src="/assets/screens-sprint.js"></script>   <!-- ADD THIS LINE -->
   ```
   Write the item-32 test as specified below; it passes once, and only once, this
   line is added. (Item 33, a different ticket, will additionally need
   `screens-backlog.js`; out of scope here.)

2. **Item 30's "propose result *with the claim env*" cannot be honoured in-fence, and
   `plan run close` is impossible.** The dispatcher mints the claim token
   (`new_claim()`, random, `dispatch/data.py:53`) into `tickets.claim_lock`, but **no
   HTTP/CLI/test surface ever returns it**: `ticket_json` deliberately omits
   `claim_lock` (`tickets/views.py:39-40`); the tick-dispatcher report's `spawned`
   entries are `{ticket_id, run_id, pid}` only (`dispatch/runtime.py:197-199`); run
   JSON and events carry no token. So a test can obtain `PLAN_RUN_ID` (from the report)
   but never `PLAN_CLAIM`. Consequently `plan run close` — whose route calls
   `require_claim` unconditionally (`dispatch/api.py:49`) — is unreachable, and a
   *claimed* `propose result` is unreachable.
   **Resolution (SPEC-sanctioned, in-fence):** the dispatched-worker's `propose result`
   is issued **claimless** — the CLI sends only `X-Plan-Actor: agent`
   (`cli/http.py:35`), which `authctx` classifies as a *non-dispatched agent context*
   (`is_claimed_agent=False`), so `propose_field` skips `require_claim`
   (`tickets/api.py:296-297`) and files a normal agent proposal. This is exactly the
   §7.6 exception ("proposal/recap writes from non-dispatched agent contexts … are
   permitted"). It reaches every **scored** assertion of SPEC item 30 (line 309):
   dispatcher claims it (run visible on Ticket), result parks in `needs_review`,
   appears in the approval queue, approve → `done`. The `plan run close` sub-step
   named only in the *ticket* (not the SPEC item) is **omitted**; note this in
   `decisions.md`. If literal claim-env fidelity is wanted later, it requires a new
   test-mode seam (e.g. add `claim` to the report's `spawned` entries, or a
   `GET /api/test/claim/{ticket_id}`) — a separate src ticket.

---

## 1. Harness recap (reuse, do not reinvent)

From `tests/e2e/conftest.py`:
- `server` — one fresh `plan serve` subprocess + fresh temp SQLite DB per test,
  `PLAN_TEST_MODE=1`, `PLAN_FAKE_NOW=2026-07-04T12:00:00`, `PLAN_WS_POLL_MS=50`,
  `PLAN_UI_DEBOUNCE_MS=50`, echo gateway, **fake** spawn/boundary adapters (auto→fake
  under test mode, `registry.py:35-58`). **`server` alone suffices for all five tests**
  — fresh DB, no cross-test state; `server_factory` is not needed.
- `open_page(ctx, server, route, ready_selector, settled)` — goes to
  `server.base + "/" + route`, waits `ready_selector`, waits `wsOpens>=1`; if
  `settled=True` also waits `flushes>=1` then re-anchors `ready_selector`. **Use
  `settled=True` whenever the DB state was mutated before the page opened** (all five
  tests open their pages after setup).
- `cli(server, *args, ticket_id=?, actor=?, run_id=?, claim=?, stdin=?)` — runs
  `plan … --json`, asserts rc==0, returns parsed JSON. Sets `PLAN_TICKET_ID`/
  `PLAN_ACTOR`/`PLAN_RUN_ID`/`PLAN_CLAIM` env only when the kwarg is given.
- `api.get(server, path)` → GET JSON (asserts <300). `api.human_post(server, path,
  json_body)` → POST JSON with **no** `X-Plan-*` headers (classified as the human;
  also the correct caller for the `/api/test/*` endpoints, which do no auth).

Module top matches flows_a: `from __future__ import annotations`, `from
playwright.sync_api import Page`, `WAIT_MS = 10_000`, and a module docstring (see §3).

**Fence rules (SPEC §18.2 / ticket):** exactly ONE test per anchor
`test_e28_`…`test_e32_` across the whole suite; every shared helper has a non-`test_`
name; no `pytest.skip`/`xfail`/`.only`/commented-out/empty bodies; no `parametrize` on
anchored tests. Assert SPEC-exact literals, never weakened.

---

## 2. Module constants (exact strings)

```python
WAIT_MS = 10_000

# planning-date boundary math (§6.1, dates.py): now-5h → calendar date.
# baseline PLAN_FAKE_NOW = 2026-07-04T12:00  → planning date 2026-07-04 ("yesterday").
# after set-now 2026-07-05T05:01 → now-5h = 2026-07-05T00:01 → planning date 2026-07-05.
NOW_0501 = "2026-07-05T05:01:00"
DAY_PREV = "2026-07-04"
DAY_CUR  = "2026-07-05"

# fake boundary adapter literals (core/adapters/fakes.py:44-83) — DEFAULT behaviors,
# unscriptable from a test (the fake lives in the server process):
FAKE_FOCUS          = "Fake focus"            # judgment root focus (fakes.py:59)
FAKE_BRIEF_H1       = "Brief for 2026-07-05"  # brief "# Brief for <date>" → <h1> text
FAKE_REPLAN_FOCUS   = "Fake replanned focus"  # replan_root root focus (fakes.py:70)
FAKE_REPLAN_CHILD   = "Fake replanned child"  # replan_child note (fakes.py:82)

# item 28
E28_TITLE = "E28 carryover ticket"

# item 29 (two carryover children so a sibling proves "others keep status")
E29_A_TITLE = "E29 child A"
E29_B_TITLE = "E29 child B"

# item 30 (ceiling needs_review so accepted result parks AT needs_review, §4.4.5)
E30_TITLE   = "E30 dispatch ticket"
E30_SUCCESS = "E30 success body."
E30_APPROACH= "E30 approach body."
E30_PLAN    = "E30 plan body."
E30_RESULT  = "E30 result body."

# item 31 (ceiling in_progress so the result proposal PARKS pending → mid-flow)
E31_TITLE   = "E31 midflow ticket"
E31_SUCCESS = "E31 success body."
E31_APPROACH= "E31 approach body."
E31_PLAN    = "E31 plan body."
E31_RESULT  = "E31 result proposal body."

# item 32
E32_SPRINT_NAME = "E32 sprint"
E32_START = "2026-07-01"          # range contains baseline planning date 2026-07-04
E32_END   = "2026-07-12"
E32_ITEM_TITLE  = "E32 item"
E32_ITEM_PROJECT= "Vylo"
E32_LOOSE_TITLE = "E32 loose ticket"
```

---

## 3. Module docstring (mirror flows_a's discipline)

```
"""E2E flows B — SPEC §18.3 items 28-32 (Playwright, chromium; two contexts in e32).

One test per acceptance item, its name carrying the ``test_eNN_`` anchor the verify
scorer matches: exactly one anchored match per item across the whole e2e suite, so no
parametrize and every shared helper below has a non-``test_`` name. Boundary/dispatch
ticks are driven synchronously through /api/test/*; time through /api/test/set-now.
Assertions use the SPEC's exact values (planning dates, fake-adapter strings, node
statuses, ticket states); every wait carries an explicit timeout and precedes its
assert — no sleeps."""
```

---

## 4. Shared non-`test_` helpers

```python
def _set_now(api, server, iso):
    r = api.human_post(server, "/api/test/set-now", {"now": iso})
    return r  # {"now":..., "planning_date":...}

def _tick_boundary(api, server):
    return api.human_post(server, "/api/test/tick-boundary", {})   # pinned 4-key report

def _tick_dispatcher(api, server):
    return api.human_post(server, "/api/test/tick-dispatcher", {})  # pinned 5-key report

def _wait_node_note(page, node, text):
    page.wait_for_function(
        "a => { const n = document.querySelector('[data-node=\"'+a.node+'\"] .plan-node-note');"
        " return !!n && n.textContent === a.text; }",
        arg={"node": str(node), "text": text}, timeout=WAIT_MS)

def _grant_and_advance(server, api, cli, tid, ceiling):
    # Human grant (header-less → human; grant_ticket rejects agents, tickets/api.py:359).
    g = api.human_post(server, f"/api/tickets/{tid}/grant",
                       {"ceiling": ceiling, "at_cap": "propose"})
    assert g["ceiling"] == ceiling and g["at_cap"] == "propose", g
    # Claimless CLI proposals auto-accept up the chain to in_progress (like flows_a e27).
    for field, body in (("success", ...), ("approach", ...), ("plan", ...)):
        cli(server, "propose", field, "--body-file", "-", ticket_id=tid, stdin=body)
    d = api.get(server, f"/api/tickets/{tid}")
    assert d["state"] == "in_progress", d
    return d

def _reload_settle(page, ready_selector):
    # open_page opens FRESH pages; after page.reload() re-apply its wait discipline.
    page.reload()
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    page.wait_for_function(
        "() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1", timeout=WAIT_MS)
```
(`_grant_and_advance` bodies come from the caller's E30_*/E31_* constants; inline the
three-item loop with the right constants per test, or pass a mapping — implementer's
choice, but keep it one helper, non-`test_`.)

---

## 5. Per-test blueprints

Screen/route facts (from `assets/`): Day route `#/day`, screen root
`section[data-screen="day"]` set by `screens-day.js:23`; Board `#/board`,
`section[data-screen="board"]`; Ticket `#/ticket/<id>`,
`section[data-screen="ticket"][data-ticket-id][data-state]` (`screens-ticket.js:427-430`);
Review `#/review`; Sprint `#/sprint`, root `[data-screen="sprint"]` (a `div`, set
`screens-sprint.js:313`). The `open_page` `route` arg includes the leading `#`
(e.g. `"#/day"`), exactly as flows_a passes it.

### test_e28_day_boundary_accept_all(server, context_factory, open_page, cli, api)

Satisfies SPEC line 307: "driving `POST /api/test/tick-boundary` at fake-now 05:01
produces the brief and a proposed tree; Accept-all adds the child tickets to the day
list." Guarantees: boundary job §6.2 (`days/boundary.py:34-90`), carryover
(`carryover.py:20-36`), fake judgment children-from-carryover (`fakes.py:50-62`),
tree render (`components.js:558-617`), accept-all + AddTicketToDay (§6.3 line 137,
`tree.py:101-114`, `days/data.py:185-207`).

1. `tid = cli(server, "ticket", "create", "--title", E28_TITLE)["id"]` — created
   `needs_success`, non-`done` → a carryover candidate.
2. Put it on the **previous** day (planning date is 2026-07-04 at baseline; use the
   explicit date to be deterministic):
   `cli(server, "day", "add-ticket", tid, DAY_PREV)` → `POST /api/day/2026-07-04/tickets`
   (`cli/main.py:514-520`; route materializes the day, `days/api.py:129-137`). This is
   yesterday's day-ticket, NOT a plan of 2026-07-05, so the §6.2 human-planned skip
   (`boundary.py:170-182`) is not triggered.
3. `_set_now(api, server, NOW_0501)` → assert returned `planning_date == DAY_CUR`.
4. `rep = _tick_boundary(api, server)`. **Assert the pinned report**
   (`scheduler.py:268-273`): `rep == {"planning_date": DAY_CUR, "ran": True,
   "judgment": "ok", "replan": None}`. (`ran` True: first tick for 2026-07-05;
   `judgment` "ok": success path recorded `boundary.py:90`.)
5. `page = open_page(context_factory(), server, "#/day",
   'section[data-screen="day"]', settled=True)`.
6. **Brief** (§6.2 stored brief → `markdownBlock`): assert
   `page.inner_text('.day-main .markdown-block h1') == FAKE_BRIEF_H1`.
   (`# Brief for 2026-07-05` → `<h1>`, `markdown.js:124-129`; day-main's only
   markdown-block, `screens-day.js:113`.)
7. **Proposed tree** (`planTree`): wait `page.wait_for_selector('.plan-tree [data-node="0"]',
   timeout=WAIT_MS)`, then assert:
   - `page.inner_text('.plan-tree [data-node="root"] .plan-node-focus') == FAKE_FOCUS`
   - `page.get_attribute('.plan-tree [data-node="root"]', "data-status") == "proposed"`
   - `page.inner_text('.plan-tree [data-node="0"] .plan-node-note') == E28_TITLE`
     (child note = carryover title, `fakes.py:53`)
   - `page.get_attribute('.plan-tree [data-node="0"]', "data-status") == "proposed"`
8. **Accept-all** (top-level control, `components.js:562-565`): capture
   `f0 = page.evaluate("window.__plannerDebug.flushes")`; `page.click('[data-accept-all]')`.
   No reload — the child ticket can only reach the Today panel via a WS-flush re-render.
9. Wait: `page.wait_for_selector(f'.day-ticket-row[data-ticket-id="{tid}"]', timeout=WAIT_MS)`.
   Assert exactly once on the day list AND a fresh flush landed:
   - `page.eval_on_selector_all(f'.day-ticket-row[data-ticket-id="{tid}"]',
     "els => els.length") == 1` (idempotent add, `days/data.py:114-119`).
   - `page.evaluate("window.__plannerDebug.flushes") > f0`.
   - Cross-check the canonical source: `d = api.get(server, "/api/day/today")`;
     `[t["id"] for t in d["tickets"]].count(tid) == 1`; and the accepted node status:
     `d["plan"]["root"]["status"] == "accepted"`, `d["plan"]["children"][0]["status"]
     == "accepted"` (accept-all cascades, `tree.py:106-109`).

### test_e29_invalidation_root_and_child(server, context_factory, open_page, cli, api)

Satisfies SPEC line 308: root invalidate → replacement tree; child invalidate →
only that child replaced, others keep status. Guarantees: `invalidate_child`/
`invalidate_root` (`tree.py:117-144`), **latest-wins replan queue is NOT executed in
the request** — the invalidate endpoint marks-invalidated + `submit_replan` (enqueue
only) post-commit (`days/api.py:191-195`), and execution happens only when
`process_pending_replan` runs, which in test mode is reachable ONLY via
`POST /api/test/tick-boundary` (`scheduler.py:227-273`; no background loops in test
mode, `server.py:92`). So every invalidate is followed by one tick-boundary.

**Ordering (justified):** `replan_root` returns a tree with **zero children**
(`fakes.py:70`). Do **child-invalidate first** (children still exist), then
root-invalidate. Both halves live in this one `test_e29_` function.

Setup (two carryover children):
1. `a = cli(server,"ticket","create","--title",E29_A_TITLE)["id"]`;
   `b = cli(server,"ticket","create","--title",E29_B_TITLE)["id"]`.
2. `cli(server,"day","add-ticket",a,DAY_PREV)` then `cli(server,"day","add-ticket",b,DAY_PREV)`
   → positions 0,1 on 2026-07-04 (`_read_yesterday_tickets` orders by position,
   `boundary.py:106-116`).
3. `_set_now(api, server, NOW_0501)`; `rep = _tick_boundary(api, server)`; assert
   `rep == {"planning_date":DAY_CUR,"ran":True,"judgment":"ok","replan":None}`.
4. `page = open_page(ctx, server, "#/day", 'section[data-screen="day"]', settled=True)`;
   `page.wait_for_selector('.plan-tree [data-node="1"]', timeout=WAIT_MS)`.
   Assert initial: `[data-node="0"] .plan-node-note == E29_A_TITLE`,
   `[data-node="1"] .plan-node-note == E29_B_TITLE`, both `data-status="proposed"`,
   root focus `FAKE_FOCUS` `data-status="proposed"`.

Child invalidate (node 0):
5. `api.human_post(server, f"/api/day/{DAY_CUR}/plan/invalidate", {"node": 0})`
   (`node` an **int**; the server rejects string positions, `days/api.py:83-90`).
   Use `api.human_post` (blocks until committed+enqueued) so the following tick sees
   the queued request — a UI click would leave an async gap where the tick could drain
   an empty queue (race). `human_post` is header-less → human → `reject_agents` passes
   (`days/api.py:181`).
6. `rep = _tick_boundary(api, server)` drains the replan. Assert
   `rep["ran"] is False` (2026-07-05 boundary already ran; idempotent guard
   `boundary.py:44`), `rep["judgment"] == "ok"`, and
   `rep["replan"] == {"attempts": [{"day_id": f"day_{DAY_CUR}", "scope": "child",
   "node": 0, "outcome": "stored"}]}` (`scheduler.py:224`).
7. `_wait_node_note(page, 0, FAKE_REPLAN_CHILD)` (WS-driven re-render). Then assert
   "only that child replaced (others keep status/note)":
   - `[data-node="0"] .plan-node-note == FAKE_REPLAN_CHILD`, `data-status="proposed"`
     (replan_child returns proposed, position preserved, `fakes.py:78-83`).
   - `[data-node="1"] .plan-node-note == E29_B_TITLE`, `data-status="proposed"` (unchanged).
   - `[data-node="root"] .plan-node-focus == FAKE_FOCUS`, `data-status="proposed"` (unchanged).

Root invalidate:
8. `api.human_post(server, f"/api/day/{DAY_CUR}/plan/invalidate", {"node": "root"})`
   (string `"root"`). This marks root + every child invalidated and enqueues one
   ReplanRoot (`tree.py:117-127`).
9. `rep = _tick_boundary(api, server)`; assert
   `rep["replan"]["attempts"] == [{"day_id": f"day_{DAY_CUR}", "scope": "root",
   "node": "root", "outcome": "stored"}]`.
10. Wait for the replacement tree to render (zero children, `fakes.py:70`):
    `page.wait_for_function("() => { const f = document.querySelector('[data-node=\"root\"]"
    " .plan-node-focus'); return !!f && f.textContent === '" + FAKE_REPLAN_FOCUS + "'"
    " && document.querySelectorAll('.plan-tree .plan-node--child').length === 0; }",
    timeout=WAIT_MS)`. Then assert:
    - `page.inner_text('[data-node="root"] .plan-node-focus') == FAKE_REPLAN_FOCUS`.
    - `page.eval_on_selector_all('.plan-tree .plan-node--child', "els=>els.length") == 0`.
    - Cross-check: `api.get(server,"/api/day/today")["plan"]` →
      `root.focus == FAKE_REPLAN_FOCUS`, `children == []`.

### test_e30_dispatcher_e2e_to_done(server, context_factory, open_page, cli, api)

Satisfies SPEC line 309 (scored assertions): tick-dispatcher claims an eligible ticket
(run visible on Ticket); the worker's `propose result` parks it in `needs_review`; it
appears in the approval queue; approve → `done`. Guarantees: eligibility §7.2
(`eligibility.py:36-59`), claim+run (`dispatch/data.py:42-67`), fake spawn ok
(`fakes.py:27-33`), result routing §4.4.5 (`resolution.py:108` + `machine.advance_target`),
approval queue "review" (`carryover.py:103-106`, `tickets/views.py:298-309`), approve
(`resolution.py:190-199`). **Claim-env caveat: see §0 finding 2 — `propose result` is
claimless; no `plan run close`.** No `set-now` needed (dispatch works at the baseline
clock; claim TTL 900s never expires under the fixed test clock).

1. `mid = cli(server,"ticket","create","--title",E30_TITLE)["id"]`.
2. `_grant_and_advance(server, api, cli, mid, "needs_review")` using E30_SUCCESS/
   APPROACH/PLAN → drives `needs_success→…→in_progress` (each target ≤ ceiling
   `needs_review` ⇒ auto-accept, `resolution.py:108`). Post-assert state `in_progress`.
3. `page = open_page(context_factory(), server, f"#/ticket/{mid}",
   f'section[data-screen="ticket"][data-ticket-id="{mid}"]', settled=True)`.
   Sanity: `page.get_attribute('section[data-screen="ticket"]',"data-state")=="in_progress"`;
   `page.query_selector('[data-marker="running-claim"]') is None`;
   Runs panel shows `(no runs)` (`page.inner_text('[data-run-history]')` contains
   `"(no runs)"`, `components.js:920-921`).
4. `rep = _tick_dispatcher(api, server)`. Assert (`runtime.py:127-218`):
   `rep["skipped"] is None`; `len(rep["spawned"]) == 1`;
   `rep["spawned"][0]["ticket_id"] == mid`; `run_id = rep["spawned"][0]["run_id"]`
   is truthy. (Only `mid` is eligible on this DB, so exactly one claim.)
5. **Run visible on Ticket, no reload** (WS flush from `run_started`):
   `page.wait_for_selector('[data-run-history] [data-run-row][data-run-status="running"]',
   timeout=WAIT_MS)`; `page.wait_for_selector('[data-marker="running-claim"]',
   timeout=WAIT_MS)`. Cross-check API: `api.get(server,f"/api/tickets/{mid}/runs")`
   → `runs[0]["status"] == "running"` and `runs[0]["id"] == run_id`.
6. Worker files the result (claimless — §0 finding 2):
   `r = cli(server, "propose", "result", "--body-file", "-", ticket_id=mid,
   stdin=E30_RESULT)`. Assert `r["state"] == "needs_review"` (auto-accept →
   `needs_review`, ceiling≠`done` ⇒ not straight to `done`, §4.4.5).
7. Ticket page flips without reload: `page.wait_for_function("() => { const s ="
   " document.querySelector('section[data-screen=\"ticket\"]'); return !!s &&"
   " s.getAttribute('data-state') === 'needs_review'; }", timeout=WAIT_MS)`.
8. Approval queue + approve via the Review "review" card (SPEC §10 screen 2):
   `card = f'[data-review-card][data-entity-id="{mid}"]'`;
   `rpage = open_page(context_factory(), server, "#/review", card, settled=True)`;
   assert `rpage.get_attribute(card, "data-kind") == "review"` (`components.js:769`);
   also cross-check `api.get(server,"/api/queues")["approvals"]` has one entry with
   `entity_id == mid`, `kind == "review"`.
9. `rpage.click(f"{card} [data-approve]")` (`components.js:727-732`; wired to
   `POST /api/tickets/{id}/approve`, `screens-review.js:123-127`). Wait
   `rpage.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)` (queue emptied).
10. Assert terminal: `api.get(server, f"/api/tickets/{mid}")["state"] == "done"`.

### test_e31_refresh_restores_state(server, context_factory, open_page, cli, api)

Satisfies SPEC line 310: on Day, Board, and Ticket **mid-flow (pending proposal,
running claim)** a reload renders identical content. Guarantees: stateless render / "refresh
restores identical state" (SPEC §10 line 191, `app.js:1-5`), board markers §10.3
(`board_view` `tickets/views.py:245-246`, `screens-board.js:34-49`), pending-proposal
parked at ceiling with `at_cap=propose` (§4.3, `resolution.py:119-139`), running claim
(dispatch).

**Mid-flow construction** — one ticket carries BOTH a pending gating-field proposal
AND a running claim. This requires claiming *before* the proposal is filed (a
gating-pending ticket is dispatch-ineligible, `eligibility.py:53`), and a ceiling that
makes the result proposal PARK rather than advance ⇒ **ceiling `in_progress`**
(target `needs_review` > ceiling ⇒ no auto-accept, but `state==ceiling==in_progress`
with `at_cap=propose` ⇒ dispatch-eligible branch (b), `eligibility.py:59`).

1. `mid = cli(server,"ticket","create","--title",E31_TITLE)["id"]`.
2. `_grant_and_advance(server, api, cli, mid, "in_progress")` with E31_SUCCESS/APPROACH/
   PLAN → `in_progress`, ceiling `in_progress`, `at_cap propose`, no claim, no pending.
3. Day plan (a "pending plan tree"): `_set_now(api, server, NOW_0501)`;
   `rep = _tick_boundary(api, server)`; assert `rep == {"planning_date":DAY_CUR,
   "ran":True,"judgment":"ok","replan":None}`. Carryover is empty (no 2026-07-04
   day-tickets) ⇒ **root-only proposed tree** (`fakes.py:59`, children=[]).
4. `rep = _tick_dispatcher(api, server)`; assert `len(rep["spawned"])==1` and
   `rep["spawned"][0]["ticket_id"]==mid` (only `mid` is eligible). `mid` now has a
   running claim + run.
5. Worker files the result (claimless): `r = cli(server,"propose","result",
   "--body-file","-",ticket_id=mid,stdin=E31_RESULT)`; assert `r["state"]=="in_progress"`
   and `r["fields"]["result"]["proposal"]["body"]==E31_RESULT` (PARKED, not advanced).
   Now: `in_progress` + pending `result` proposal + active claim = the mid-flow state.

Three surfaces. Define stable snapshots (content-bearing, timestamp-free) and assert
`before == after` AND the exact values.

**Ticket** (`page_t`), ready `f'section[data-screen="ticket"][data-ticket-id="{mid}"]'`,
mid-flow selector `[data-field="result"] .proposal-card`:
```python
def _snap_ticket(p):
    return {
      "state": p.get_attribute('section[data-screen="ticket"]', "data-state"),
      "meta":  p.inner_text('[data-field="result"] .proposal-card .proposal-meta'),
      "body":  p.inner_text('[data-field="result"] .proposal-card .markdown-block'),
      "claim": p.eval_on_selector_all('[data-marker="running-claim"]', "e=>e.length"),
      "run":   p.eval_on_selector_all(
                 '[data-run-history] [data-run-row][data-run-status="running"]', "e=>e.length"),
    }
```
Expected: `state=="in_progress"`, `meta=="proposed by agent"` (`components.js:492`;
proposer is `PLAN_ACTOR` default `agent`), `body==E31_RESULT`, `claim==1`
(header marker `screens-ticket.js:129-131`), `run==1`.

**Board** (`page_b`), ready `'section[data-screen="board"]'`, mid-flow selector
`f'[data-column="in_progress"] [data-card][data-ticket-id="{mid}"]'`:
```python
def _snap_board(p):
    card = f'[data-column="in_progress"] [data-card][data-ticket-id="{mid}"]'
    return {
      "title": p.inner_text(f'{card} .entity-row-title'),
      "pend":  p.eval_on_selector_all(f'{card} [data-marker="pending-proposal"]', "e=>e.length"),
      "claim": p.eval_on_selector_all(f'{card} [data-marker="running-claim"]', "e=>e.length"),
    }
```
Expected: `title==E31_TITLE`, `pend==1`, `claim==1` (`board_view` sets
`has_pending_proposal`+`has_running_claim`, markers `screens-board.js:39-40`).

**Day** (`page_d`), ready `'.plan-tree [data-node="root"]'`, mid-flow selector same:
```python
def _snap_day(p):
    return {
      "focus":  p.inner_text('[data-node="root"] .plan-node-focus'),
      "status": p.get_attribute('[data-node="root"]', "data-status"),
      "brief":  p.inner_text('.day-main .markdown-block h1'),
    }
```
Expected: `focus==FAKE_FOCUS`, `status=="proposed"`, `brief==FAKE_BRIEF_H1`.

6. Open all three with `settled=True`. For each: wait its mid-flow selector, take
   `before = _snap_*(page)`, then `_reload_settle(page, ready_selector)`, wait the
   mid-flow selector again, take `after = _snap_*(page)`. Assert `before == after`
   **and** `before == <expected dict>` (both, so "identical" isn't vacuously
   satisfied by two identically-broken renders).

### test_e32_sprint_live_status_and_loose(server, context_factory, open_page, cli, api)

Satisfies SPEC line 311: `plan item set --status active` reflects on the Sprint screen
in both contexts without reload; loose ticket appears in the loose section. Guarantees:
agent `todo→active` (§3.2 line 41, `sprints/data.py:390-434`, `classify_agent_transition`),
sprint-current view groups + loose tickets §5 (`sprints/views.py:195-230`), Sprint
render (`screens-sprint.js`). **Requires the §0-finding-1 shell edit; without it every
step below times out at `open_page`.** No `set-now` — baseline planning date 2026-07-04
lies inside `[E32_START, E32_END]`, so `/api/sprint/current` resolves this sprint
(`sprints/logic.current_sprint_id`).

1. Create the current sprint (human-only; there is no `plan sprint create` verb —
   `cli/main.py:443-459` has only `sprint show`): `s = api.human_post(server,
   "/api/sprints", {"name":E32_SPRINT_NAME, "date_start":E32_START, "date_end":E32_END})`;
   `sid = s["id"]` (`sprints/api.py:254-273`).
2. Item in the sprint (defaults status `todo`): `iid = cli(server,"item","create",
   "--title",E32_ITEM_TITLE,"--project",E32_ITEM_PROJECT,"--sprint",sid)["id"]`
   (`cli/main.py:351-375` → `POST /api/items` with `sprint_id`).
3. Loose ticket (sprint set, no parent item): `ltid = cli(server,"ticket","create",
   "--title",E32_LOOSE_TITLE,"--sprint",sid)["id"]` (`--sprint`→`sprint_id`,
   `sprint_item_id` stays NULL ⇒ loose, `sprints/views.py:220-225`).
4. Open two Sprint contexts (state preceded ⇒ `settled=True`):
   `ready = '[data-status-group="active"]'`;
   `pa = open_page(context_factory(), server, "#/sprint", ready, settled=True)`;
   `pb = open_page(context_factory(), server, "#/sprint", ready, settled=True)`.
   The Items panel always renders all five status groups (`screens-sprint.js:133-136`),
   so `ready` is present once the sprint view loads.
5. Initial assertions (both pages):
   - item in `todo`: `p.wait_for_selector(f'[data-status-group="todo"]
     [data-item-id="{iid}"]', timeout=WAIT_MS)`.
   - loose ticket present: `assert p.query_selector(f'[data-loose]
     [data-ticket-id="{ltid}"]') is not None` (`screens-sprint.js:213-234`) — satisfies
     the "loose ticket appears in the loose section" clause.
6. Capture flush counters: `fa = pa.evaluate("window.__plannerDebug.flushes")`;
   `fb = pb.evaluate("window.__plannerDebug.flushes")`.
7. `cli(server, "item", "set", iid, "--status", "active")` →
   `PATCH /api/items/{iid} {"status":"active"}`, `by_agent=True` (CLI sends
   `X-Plan-Actor: agent`), agent `todo→active` permitted (`sprints/api.py:225-230`).
8. Live move in BOTH contexts, no reload (WS flush → refetch → re-render):
   for `p, f0` in `((pa, fa), (pb, fb))`:
   - `p.wait_for_selector(f'[data-status-group="active"] [data-item-id="{iid}"]',
     timeout=WAIT_MS)`.
   - assert it left `todo`: `p.query_selector(f'[data-status-group="todo"]
     [data-item-id="{iid}"]') is None`.
   - assert a fresh flush drove it: `p.evaluate("window.__plannerDebug.flushes") > f0`.
9. Cross-check canonical source: `api.get(server, "/api/sprint/current")` →
   the item id appears under `groups["active"]`, not `groups["todo"]`; `ltid` in
   `[t["id"] for t in loose_tickets]`.

---

## 6. Risks & race notes (each neutralized; wait-on-condition then assert)

- **Replan is NOT synchronous (e29).** `POST …/plan/invalidate` only marks-invalidated
  and *enqueues* (`days/api.py:191-195`); execution is in `process_pending_replan`,
  reachable in test mode ONLY via `POST /api/test/tick-boundary` (`scheduler.py:265`;
  no background loops, `server.py:92`). Neutralize: after every invalidate, issue one
  `_tick_boundary` and assert its `replan.attempts[…].outcome == "stored"` BEFORE the
  DOM wait. Issue the invalidate via `api.human_post` (blocks until commit+enqueue) so
  the tick cannot drain an empty queue.
- **e29 ordering hazard.** `replan_root` returns zero children (`fakes.py:70`), so
  child-invalidate MUST precede root-invalidate — otherwise no child remains to
  invalidate. Encoded in the step order.
- **WS-flush timing (e28 accept-all, e30 run/flip, e32 status move).** Every observed
  mutation is asserted only after `wait_for_selector`/`wait_for_function` on the
  post-flush condition (rows present, `data-state`, status-group membership), never a
  bare read; flush progress is additionally asserted via `window.__plannerDebug.flushes`
  where "no reload" is the claim (e28, e32) — the flows_a pattern.
- **`open_page` opens fresh pages; `page.reload()` does not (e31).** `_reload_settle`
  re-applies ready-selector + `wsOpens>=1`, and each snapshot is taken only after its
  mid-flow selector re-appears post-reload. The fixed test clock (never advanced past
  05:01) keeps the 900s claim active, so `running-claim`/running-run markers persist
  identically across the reload. Snapshots exclude timestamps (`run-history-times`,
  `formatUnix`) — only statuses, notes, markers, bodies, and `data-state` are compared.
- **Dispatcher claims exactly the intended ticket (e30, e31).** Each test's DB has a
  single dispatch-eligible ticket at tick time, so `spawned` has length 1; asserted.
  (In e31, ceiling `in_progress` + `at_cap propose` is what makes `mid` eligible at
  `in_progress` via branch (b); after the result parks, it becomes ineligible but is
  already claimed.)
- **Claim-env unobtainable (e30) / shell gap (e32).** See §0. e30 uses the §7.6
  claimless path and omits `plan run close`; e32 depends on the one-line `server.py`
  shell edit. Both flagged for the integrator; the test bodies below assume the shell
  edit is applied.
- **`ran`/`judgment` report values.** First tick per date: `ran True`, `judgment "ok"`.
  A second tick same date (e29 drains): `ran False`, `judgment "ok"` (idempotent guard,
  `boundary.py:44`). Pinned in the report asserts.

## 7. Fence-compliance checklist

- Exactly one anchored test each: `test_e28_…`, `test_e29_…`, `test_e30_…`,
  `test_e31_…`, `test_e32_…`. No other `test_`-prefixed names.
- Helpers `_set_now`, `_tick_boundary`, `_tick_dispatcher`, `_wait_node_note`,
  `_grant_and_advance`, `_reload_settle`, `_snap_ticket`, `_snap_board`, `_snap_day`
  are all non-`test_`.
- No `parametrize`, no `pytest.skip`/`xfail`/`.only`, no commented-out tests, no empty
  bodies. Module docstring present (§3).
- All asserted values are SPEC-exact literals (planning dates, fake-adapter strings,
  node statuses, ticket states, report shapes), never weakened.
```

---

## 8. BINDING AMENDMENTS (orchestrator, post codex plan review — override the body above)

**A1 — Day ready selectors (codex F1, accepted).** There is no Day `<section>`:
app.js renders every screen into a `div` (app.js:59-62) and screens-day.js:23 sets
`data-screen="day"` on that div (only Board/Ticket build their own `<section>`).
Replace every `'section[data-screen="day"]'` ready selector:
- e28 step 5: `open_page(..., "#/day", '.plan-tree [data-node="0"]', settled=True)`
  (step 7's separate wait for `[data-node="0"]` becomes redundant; keep the assertions).
- e29 step 4: `open_page(..., "#/day", '.plan-tree [data-node="1"]', settled=True)`.
- e31 Day page already uses `'.plan-tree [data-node="root"]'` — unchanged.
Board (`section[data-screen="board"]`) and Ticket (`section[data-screen="ticket"]…`)
selectors are correct as planned and unchanged.

**A2 — `_reload_settle` full settled discipline (codex F2, accepted).** After
`page.reload()` the `__plannerDebug` counters reset and the since=0 catch-up replay
fires one flush; the "after" snapshot must not race it. Final helper:

```python
def _reload_settle(page, ready_selector):
    page.reload()
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    page.wait_for_function(
        "() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1",
        timeout=WAIT_MS)
    page.wait_for_function(
        "() => window.__plannerDebug && window.__plannerDebug.flushes >= 1",
        timeout=WAIT_MS)
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
```
Callers still re-wait their mid-flow selector before taking the "after" snapshot.

**A3 — e30 is SPEC-LITERAL: claim env + `plan run close` (replaces §0 finding 2 and
every "claimless" step in the e30 blueprint; §0 finding 2's premise was wrong).**
The claim token lives in `tickets.claim_lock` (dispatch/data.py:55) and the test owns
its server's temp DB via `server.db_path` — read it there. The DB is WAL
(core/db.py:152), so use a plain connection (not a `mode=ro` URI), one SELECT, close
immediately:

```python
import sqlite3  # module top

def _read_claim(server, ticket_id):
    conn = sqlite3.connect(str(server.db_path))
    try:
        row = conn.execute(
            "SELECT claim_lock FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0], row
    return str(row[0])
```

e30 steps 6-7 become (steps 1-5 and 8-10 unchanged except as noted):
6a. `token = _read_claim(server, mid)` (after the tick; the claim CAS has committed —
    the tick report returned).
6b. Worker proposes WITH the claim env (validated by `require_claim`,
    tickets/api.py:296-297; the resolution engine never consults the claim, so the
    auto-accept to `needs_review` is unchanged):
    `r = cli(server, "propose", "result", "--body-file", "-", ticket_id=mid,
    run_id=run_id, claim=token, stdin=E30_RESULT)`; assert `r["state"] == "needs_review"`.
7.  Ticket page flips without reload (unchanged wait for `data-state == "needs_review"`).
7b. Worker closes the run: `c = cli(server, "run", "close", "--outcome", "done",
    run_id=run_id, claim=token)` (`require_claim` resolves run→ticket,
    dispatch/api.py:49-56; claim still active — nothing between claim and close
    cleared it). Assert `c["run"]["status"] == "done"` and
    `c["run"]["id"] == run_id`. Ticket state is untouched by close ⇒ still parked
    `needs_review`; cross-check `api.get(server, f"/api/tickets/{mid}")["state"] ==
    "needs_review"`.
7c. `run_closed` is a logged event (dispatch/data.py:206) ⇒ flush-driven UI update:
    `page.wait_for_selector('[data-run-history] [data-run-row][data-run-status="done"]',
    timeout=WAIT_MS)`; the claim was cleared by close, so ALSO wait for the
    running-claim marker to leave:
    `page.wait_for_function("() => document.querySelector('[data-marker=\"running-claim\"]') === null",
    timeout=WAIT_MS)`.
Then steps 8-10 (review queue "review" card → `[data-approve]` → `[data-review-empty]`
→ API state `done`) exactly as written. Update the module docstring/§6 note: no
claimless caveat remains; item 30 and the ticket's `run close` sub-step are both
honoured literally.

**A4 — e31 propose stays claimless (intentional, unchanged).** The parked result
proposal in e31 is filed WITHOUT the claim env: a non-dispatched agent proposal is
the §7.6-permitted path and the parking behavior is identical; the claim env is not
needed to construct the mid-flow state. (e31 never closes its run — the running claim
IS the mid-flow.)

**A5 — e32 external dependency (status).** The `server.py` shell fix (add
`screens-sprint.js`) is escalated to the team lead and is being applied out-of-fence;
write e32 to the final selectors exactly as §5 specifies. If the implementer runs the
suite before the shell fix lands, e28-e31 must already pass; e32's failure mode is
`open_page` timeout on `[data-status-group="active"]` and is NOT a test bug.
