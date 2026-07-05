"""E2E flows B — SPEC §18.3 items 28-32 (Playwright, chromium; two contexts in e32).

One test per acceptance item, its name carrying the ``test_eNN_`` anchor the verify
scorer matches: exactly one anchored match per item across the whole e2e suite, so no
parametrize and every shared helper below has a non-``test_`` name. Boundary/dispatch
ticks are driven synchronously through /api/test/*; time through /api/test/set-now.
Assertions use the SPEC's exact values (planning dates, fake-adapter strings, node
statuses, ticket states); every wait carries an explicit timeout and precedes its
assert — no sleeps."""

from __future__ import annotations

import sqlite3

from playwright.sync_api import Page

WAIT_MS = 10_000

# planning-date boundary math (§6.1, dates.py): now-5h → calendar date.
# baseline PLAN_FAKE_NOW = 2026-07-04T12:00  → planning date 2026-07-04 ("yesterday").
# after set-now 2026-07-05T05:01 → now-5h = 2026-07-05T00:01 → planning date 2026-07-05.
NOW_0501 = "2026-07-05T05:01:00"
DAY_PREV = "2026-07-04"
DAY_CUR = "2026-07-05"

# fake boundary adapter literals (core/adapters/fakes.py:44-83) — DEFAULT behaviors,
# unscriptable from a test (the fake lives in the server process):
FAKE_FOCUS = "Fake focus"                # judgment root focus (fakes.py:59)
FAKE_BRIEF_H1 = "Brief for 2026-07-05"   # brief "# Brief for <date>" → <h1> text
FAKE_REPLAN_FOCUS = "Fake replanned focus"  # replan_root root focus (fakes.py:70)
FAKE_REPLAN_CHILD = "Fake replanned child"  # replan_child note (fakes.py:82)

# item 28
E28_TITLE = "E28 carryover ticket"

# item 29 (two carryover children so a sibling proves "others keep status")
E29_A_TITLE = "E29 child A"
E29_B_TITLE = "E29 child B"

# item 30 (ceiling needs_review so accepted result parks AT needs_review, §4.4.5)
E30_TITLE = "E30 dispatch ticket"
E30_SUCCESS = "E30 success body."
E30_APPROACH = "E30 approach body."
E30_PLAN = "E30 plan body."
E30_RESULT = "E30 result body."

# item 31 (ceiling in_progress so the result proposal PARKS pending → mid-flow)
E31_TITLE = "E31 midflow ticket"
E31_SUCCESS = "E31 success body."
E31_APPROACH = "E31 approach body."
E31_PLAN = "E31 plan body."
E31_RESULT = "E31 result proposal body."

# item 32
E32_SPRINT_NAME = "E32 sprint"
E32_START = "2026-07-01"          # range contains baseline planning date 2026-07-04
E32_END = "2026-07-12"
E32_ITEM_TITLE = "E32 item"
E32_ITEM_PROJECT = "Vylo"
E32_LOOSE_TITLE = "E32 loose ticket"


def _set_now(api, server, iso):
    return api.human_post(server, "/api/test/set-now", {"now": iso})


def _tick_boundary(api, server):
    return api.human_post(server, "/api/test/tick-boundary", {})


def _tick_dispatcher(api, server):
    return api.human_post(server, "/api/test/tick-dispatcher", {})


def _read_claim(server, ticket_id):
    conn = sqlite3.connect(str(server.db_path))
    try:
        row = conn.execute(
            "SELECT claim_lock FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0], row
    return str(row[0])


def _wait_node_note(page: Page, node, text):
    page.wait_for_function(
        "a => { const n = document.querySelector('[data-node=\"'+a.node+'\"] .plan-node-note');"
        " return !!n && n.textContent === a.text; }",
        arg={"node": str(node), "text": text},
        timeout=WAIT_MS,
    )


def _grant_and_advance(server, api, cli, tid, ceiling, bodies):
    # Human grant (header-less → human; grant_ticket rejects agents, tickets/api.py:359).
    g = api.human_post(
        server, f"/api/tickets/{tid}/grant", {"ceiling": ceiling, "at_cap": "propose"}
    )
    assert g["ceiling"] == ceiling and g["at_cap"] == "propose", g
    # Claimless CLI proposals auto-accept up the chain to in_progress (like flows_a e27).
    for field in ("success", "approach", "plan"):
        cli(server, "propose", field, "--body-file", "-", ticket_id=tid, stdin=bodies[field])
    d = api.get(server, f"/api/tickets/{tid}")
    assert d["state"] == "in_progress", d
    return d


def _reload_settle(page: Page, ready_selector):
    # open_page opens FRESH pages; after page.reload() the __plannerDebug counters
    # reset and the since=0 catch-up replay fires one flush — re-apply full discipline.
    page.reload()
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    page.wait_for_function(
        "() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1",
        timeout=WAIT_MS,
    )
    page.wait_for_function(
        "() => window.__plannerDebug && window.__plannerDebug.flushes >= 1",
        timeout=WAIT_MS,
    )
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)


def _snap_ticket(p: Page):
    return {
        "state": p.get_attribute('section[data-screen="ticket"]', "data-state"),
        "meta": p.inner_text('[data-field="result"] .proposal-card .proposal-meta'),
        "body": p.inner_text('[data-field="result"] .proposal-card .markdown-block'),
        "claim": p.eval_on_selector_all('[data-marker="running-claim"]', "e=>e.length"),
        "run": p.eval_on_selector_all(
            '[data-run-history] [data-run-row][data-run-status="running"]', "e=>e.length"
        ),
    }


def _snap_board(p: Page, mid):
    card = f'[data-column="in_progress"] [data-card][data-ticket-id="{mid}"]'
    return {
        "title": p.inner_text(f"{card} .entity-row-title"),
        "pend": p.eval_on_selector_all(f'{card} [data-marker="pending-proposal"]', "e=>e.length"),
        "claim": p.eval_on_selector_all(f'{card} [data-marker="running-claim"]', "e=>e.length"),
    }


def _snap_day(p: Page):
    return {
        "focus": p.inner_text('[data-node="root"] .plan-node-focus'),
        "status": p.get_attribute('[data-node="root"]', "data-status"),
        "brief": p.inner_text('.day-main .markdown-block h1'),
    }


def test_e28_day_boundary_accept_all(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", E28_TITLE)["id"]
    # Yesterday's day-ticket (planning date 2026-07-04 at baseline): a carryover
    # candidate, NOT a plan of 2026-07-05, so the §6.2 human-planned skip is not tripped.
    cli(server, "day", "add-ticket", tid, DAY_PREV)

    r = _set_now(api, server, NOW_0501)
    assert r["planning_date"] == DAY_CUR, r

    rep = _tick_boundary(api, server)
    assert rep == {
        "planning_date": DAY_CUR,
        "ran": True,
        "judgment": "ok",
        "replan": None,
    }, rep

    page = open_page(
        context_factory(), server, "#/day", '.plan-tree [data-node="0"]', settled=True
    )

    # Brief: the stored "# Brief for 2026-07-05" markdown → the day-main's only <h1>.
    assert page.inner_text('.day-main .markdown-block h1') == FAKE_BRIEF_H1

    # Proposed tree: root focus + carryover child, both proposed.
    assert page.inner_text('.plan-tree [data-node="root"] .plan-node-focus') == FAKE_FOCUS
    assert page.get_attribute('.plan-tree [data-node="root"]', "data-status") == "proposed"
    assert page.inner_text('.plan-tree [data-node="0"] .plan-node-note') == E28_TITLE
    assert page.get_attribute('.plan-tree [data-node="0"]', "data-status") == "proposed"

    # Accept-all: the child ticket reaches the Today panel only via a WS-flush re-render.
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.click('[data-accept-all]')

    row = f'.day-ticket-row[data-ticket-id="{tid}"]'
    page.wait_for_selector(row, timeout=WAIT_MS)
    assert page.eval_on_selector_all(row, "els => els.length") == 1
    assert page.evaluate("window.__plannerDebug.flushes") > f0

    d = api.get(server, "/api/day/today")
    assert [t["id"] for t in d["tickets"]].count(tid) == 1, d
    assert d["plan"]["root"]["status"] == "accepted", d
    assert d["plan"]["children"][0]["status"] == "accepted", d


def test_e29_invalidation_root_and_child(server, context_factory, open_page, cli, api):
    a = cli(server, "ticket", "create", "--title", E29_A_TITLE)["id"]
    b = cli(server, "ticket", "create", "--title", E29_B_TITLE)["id"]
    cli(server, "day", "add-ticket", a, DAY_PREV)
    cli(server, "day", "add-ticket", b, DAY_PREV)

    _set_now(api, server, NOW_0501)
    rep = _tick_boundary(api, server)
    assert rep == {
        "planning_date": DAY_CUR,
        "ran": True,
        "judgment": "ok",
        "replan": None,
    }, rep

    page = open_page(
        context_factory(), server, "#/day", '.plan-tree [data-node="1"]', settled=True
    )
    assert page.inner_text('.plan-tree [data-node="0"] .plan-node-note') == E29_A_TITLE
    assert page.inner_text('.plan-tree [data-node="1"] .plan-node-note') == E29_B_TITLE
    assert page.get_attribute('.plan-tree [data-node="0"]', "data-status") == "proposed"
    assert page.get_attribute('.plan-tree [data-node="1"]', "data-status") == "proposed"
    assert page.inner_text('.plan-tree [data-node="root"] .plan-node-focus') == FAKE_FOCUS
    assert page.get_attribute('.plan-tree [data-node="root"]', "data-status") == "proposed"

    # Child invalidate (node 0, an int): mark-invalidated + enqueue; the tick drains it.
    api.human_post(server, f"/api/day/{DAY_CUR}/plan/invalidate", {"node": 0})
    rep = _tick_boundary(api, server)
    assert rep == {
        "planning_date": DAY_CUR,   # second tick same date: idempotent, judgment kept
        "ran": False,
        "judgment": "ok",
        "replan": {
            "attempts": [
                {"day_id": f"day_{DAY_CUR}", "scope": "child", "node": 0, "outcome": "stored"}
            ]
        },
    }, rep

    _wait_node_note(page, 0, FAKE_REPLAN_CHILD)
    assert page.inner_text('.plan-tree [data-node="0"] .plan-node-note') == FAKE_REPLAN_CHILD
    assert page.get_attribute('.plan-tree [data-node="0"]', "data-status") == "proposed"
    # Sibling and root untouched.
    assert page.inner_text('.plan-tree [data-node="1"] .plan-node-note') == E29_B_TITLE
    assert page.get_attribute('.plan-tree [data-node="1"]', "data-status") == "proposed"
    assert page.inner_text('.plan-tree [data-node="root"] .plan-node-focus') == FAKE_FOCUS
    assert page.get_attribute('.plan-tree [data-node="root"]', "data-status") == "proposed"

    # Root invalidate ("root", a str): marks root + every child, enqueues one ReplanRoot.
    api.human_post(server, f"/api/day/{DAY_CUR}/plan/invalidate", {"node": "root"})
    rep = _tick_boundary(api, server)
    assert rep == {
        "planning_date": DAY_CUR,
        "ran": False,
        "judgment": "ok",
        "replan": {
            "attempts": [
                {"day_id": f"day_{DAY_CUR}", "scope": "root", "node": "root", "outcome": "stored"}
            ]
        },
    }, rep

    page.wait_for_function(
        "() => { const f = document.querySelector('[data-node=\"root\"] .plan-node-focus');"
        " return !!f && f.textContent === '" + FAKE_REPLAN_FOCUS + "'"
        " && document.querySelectorAll('.plan-tree .plan-node--child').length === 0; }",
        timeout=WAIT_MS,
    )
    assert page.inner_text('[data-node="root"] .plan-node-focus') == FAKE_REPLAN_FOCUS
    assert page.eval_on_selector_all('.plan-tree .plan-node--child', "els=>els.length") == 0

    plan = api.get(server, "/api/day/today")["plan"]
    assert plan["root"]["focus"] == FAKE_REPLAN_FOCUS, plan
    assert plan["children"] == [], plan


def test_e30_dispatcher_e2e_to_done(server, context_factory, open_page, cli, api):
    mid = cli(server, "ticket", "create", "--title", E30_TITLE)["id"]
    _grant_and_advance(
        server, api, cli, mid, "needs_review",
        {"success": E30_SUCCESS, "approach": E30_APPROACH, "plan": E30_PLAN},
    )

    ready = f'section[data-screen="ticket"][data-ticket-id="{mid}"]'
    page = open_page(context_factory(), server, f"#/ticket/{mid}", ready, settled=True)
    assert page.get_attribute('section[data-screen="ticket"]', "data-state") == "in_progress"
    assert page.query_selector('[data-marker="running-claim"]') is None
    assert "(no runs)" in page.inner_text('[data-run-history]')

    rep = _tick_dispatcher(api, server)
    assert set(rep) == {"skipped", "reclaimed", "timed_out", "spawned", "spawn_failed"}, rep
    assert rep["skipped"] is None, rep
    assert rep["reclaimed"] == [], rep
    assert rep["timed_out"] == [], rep
    assert rep["spawn_failed"] == [], rep
    [spawn] = rep["spawned"]
    assert spawn["ticket_id"] == mid, rep
    assert spawn["pid"] == 90001, rep   # first fake pid, fresh server per test (fakes.py:18)
    run_id = spawn["run_id"]
    assert run_id, rep

    # Run visible on Ticket, no reload — the WS flush from run_started re-renders it.
    page.wait_for_selector(
        '[data-run-history] [data-run-row][data-run-status="running"]', timeout=WAIT_MS
    )
    page.wait_for_selector('[data-marker="running-claim"]', timeout=WAIT_MS)
    runs = api.get(server, f"/api/tickets/{mid}/runs")["runs"]
    assert runs[0]["status"] == "running", runs
    assert runs[0]["id"] == run_id, runs

    # Worker files the result WITH the claim env read from the server's temp DB.
    token = _read_claim(server, mid)
    r = cli(
        server, "propose", "result", "--body-file", "-",
        ticket_id=mid, run_id=run_id, claim=token, stdin=E30_RESULT,
    )
    assert r["state"] == "needs_review", r
    assert r["fields"]["result"]["value"] == E30_RESULT, r      # auto-accept stored the body
    assert r["fields"]["result"]["proposal"] is None, r

    # Ticket page flips without reload.
    page.wait_for_function(
        "() => { const s = document.querySelector('section[data-screen=\"ticket\"]');"
        " return !!s && s.getAttribute('data-state') === 'needs_review'; }",
        timeout=WAIT_MS,
    )

    # Worker closes the run (claim still active — nothing between claim and close cleared it).
    c = cli(server, "run", "close", "--outcome", "done", run_id=run_id, claim=token)
    assert c["run"]["status"] == "done", c
    assert c["run"]["id"] == run_id, c
    assert api.get(server, f"/api/tickets/{mid}")["state"] == "needs_review", "close leaves state"

    # run_closed is a logged event → the run flips to done and the claim marker leaves.
    page.wait_for_selector(
        '[data-run-history] [data-run-row][data-run-status="done"]', timeout=WAIT_MS
    )
    page.wait_for_function(
        "() => document.querySelector('[data-marker=\"running-claim\"]') === null",
        timeout=WAIT_MS,
    )

    # Approval queue + approve via the Review "review" card.
    card = f'[data-review-card][data-entity-id="{mid}"]'
    rpage = open_page(context_factory(), server, "#/review", card, settled=True)
    assert rpage.get_attribute(card, "data-kind") == "review"
    # The review card renders the accepted result value (components.js reviewCard).
    assert rpage.inner_text(f"{card} .markdown-block") == E30_RESULT
    approvals = api.get(server, "/api/queues")["approvals"]
    assert len(approvals) == 1, approvals
    assert approvals[0]["entity_id"] == mid, approvals
    assert approvals[0]["kind"] == "review", approvals

    rpage.click(f"{card} [data-approve]")
    rpage.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)
    assert api.get(server, f"/api/tickets/{mid}")["state"] == "done"


def test_e31_refresh_restores_state(server, context_factory, open_page, cli, api):
    mid = cli(server, "ticket", "create", "--title", E31_TITLE)["id"]
    _grant_and_advance(
        server, api, cli, mid, "in_progress",
        {"success": E31_SUCCESS, "approach": E31_APPROACH, "plan": E31_PLAN},
    )

    # A pending plan tree on Day (root-only: no 2026-07-04 day-tickets → empty carryover).
    _set_now(api, server, NOW_0501)
    rep = _tick_boundary(api, server)
    assert rep == {
        "planning_date": DAY_CUR,
        "ran": True,
        "judgment": "ok",
        "replan": None,
    }, rep

    rep = _tick_dispatcher(api, server)
    assert set(rep) == {"skipped", "reclaimed", "timed_out", "spawned", "spawn_failed"}, rep
    assert rep["skipped"] is None, rep
    assert rep["reclaimed"] == [], rep
    assert rep["timed_out"] == [], rep
    assert rep["spawn_failed"] == [], rep
    [spawn] = rep["spawned"]
    assert spawn["ticket_id"] == mid, rep
    assert spawn["pid"] == 90001, rep   # first fake pid, fresh server per test (fakes.py:18)

    # Worker files the result claimless (§7.6); ceiling in_progress ⇒ it PARKS pending.
    r = cli(server, "propose", "result", "--body-file", "-", ticket_id=mid, stdin=E31_RESULT)
    assert r["state"] == "in_progress", r
    assert r["fields"]["result"]["proposal"]["body"] == E31_RESULT, r

    # Ticket surface.
    ready_t = f'section[data-screen="ticket"][data-ticket-id="{mid}"]'
    mid_t = '[data-field="result"] .proposal-card'
    page_t = open_page(context_factory(), server, f"#/ticket/{mid}", ready_t, settled=True)
    page_t.wait_for_selector(mid_t, timeout=WAIT_MS)
    before_t = _snap_ticket(page_t)
    _reload_settle(page_t, ready_t)
    page_t.wait_for_selector(mid_t, timeout=WAIT_MS)
    after_t = _snap_ticket(page_t)
    expected_t = {
        "state": "in_progress",
        "meta": "proposed by agent",
        "body": E31_RESULT,
        "claim": 1,
        "run": 1,
    }
    assert before_t == after_t == expected_t, (before_t, after_t)

    # Board surface.
    ready_b = 'section[data-screen="board"]'
    mid_b = f'[data-column="in_progress"] [data-card][data-ticket-id="{mid}"]'
    page_b = open_page(context_factory(), server, "#/board", ready_b, settled=True)
    page_b.wait_for_selector(mid_b, timeout=WAIT_MS)
    before_b = _snap_board(page_b, mid)
    _reload_settle(page_b, ready_b)
    page_b.wait_for_selector(mid_b, timeout=WAIT_MS)
    after_b = _snap_board(page_b, mid)
    expected_b = {"title": E31_TITLE, "pend": 1, "claim": 1}
    assert before_b == after_b == expected_b, (before_b, after_b)

    # Day surface.
    ready_d = '.plan-tree [data-node="root"]'
    page_d = open_page(context_factory(), server, "#/day", ready_d, settled=True)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    before_d = _snap_day(page_d)
    _reload_settle(page_d, ready_d)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    after_d = _snap_day(page_d)
    expected_d = {"focus": FAKE_FOCUS, "status": "proposed", "brief": FAKE_BRIEF_H1}
    assert before_d == after_d == expected_d, (before_d, after_d)


def test_e32_sprint_live_status_and_loose(server, context_factory, open_page, cli, api):
    s = api.human_post(
        server,
        "/api/sprints",
        {"name": E32_SPRINT_NAME, "date_start": E32_START, "date_end": E32_END},
    )
    sid = s["id"]

    iid = cli(
        server, "item", "create", "--title", E32_ITEM_TITLE,
        "--project", E32_ITEM_PROJECT, "--sprint", sid,
    )["id"]
    ltid = cli(server, "ticket", "create", "--title", E32_LOOSE_TITLE, "--sprint", sid)["id"]

    ready = '[data-status-group="active"]'
    pa = open_page(context_factory(), server, "#/sprint", ready, settled=True)
    pb = open_page(context_factory(), server, "#/sprint", ready, settled=True)

    for p in (pa, pb):
        p.wait_for_selector(f'[data-status-group="todo"] [data-item-id="{iid}"]', timeout=WAIT_MS)
        assert p.query_selector(f'[data-loose] [data-ticket-id="{ltid}"]') is not None

    fa = pa.evaluate("window.__plannerDebug.flushes")
    fb = pb.evaluate("window.__plannerDebug.flushes")

    # Agent todo→active (§3.2): PATCH /api/items/{iid} with X-Plan-Actor: agent.
    cli(server, "item", "set", iid, "--status", "active")

    for p, f0 in ((pa, fa), (pb, fb)):
        p.wait_for_selector(
            f'[data-status-group="active"] [data-item-id="{iid}"]', timeout=WAIT_MS
        )
        assert p.query_selector(f'[data-status-group="todo"] [data-item-id="{iid}"]') is None
        assert p.evaluate("window.__plannerDebug.flushes") > f0

    cur = api.get(server, "/api/sprint/current")
    assert iid in [i["id"] for i in cur["groups"]["active"]], cur
    assert iid not in [i["id"] for i in cur["groups"]["todo"]], cur
    assert ltid in [t["id"] for t in cur["loose_tickets"]], cur
