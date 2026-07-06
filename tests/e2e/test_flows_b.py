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

# fake boundary adapter literals (core/adapters/fakes.py) — DEFAULT behaviors,
# unscriptable from a test (the fake lives in the server process). It fills the four
# overview fields keyed off the planning date:
FAKE_FOCUS = "Focus for 2026-07-05"
FAKE_TAKE = "Brief take for 2026-07-05"
FAKE_WATCH = "Watchout for 2026-07-05"
FAKE_LANDS = "If today lands for 2026-07-05"

# item 28 — the boundary FILLS the four overview fields; the Day overview renders each
# in its own slot (focus hero + Brief Take / Watchout / If Today Lands bodies).

# item 29 — the four overview fields, seeded per-field through the human PATCH, then
# amended in place. Each body is a single line so its slot's inner_text is exactly the
# source text.
E29_FOCUS = "E29 ship the waitlist funnel to real traffic."
E29_TAKE = "E29 yesterday closed at sixty-two percent completion."
E29_WATCH = "E29 the hero still does not say what Vylo is in one line."
E29_LANDS = "E29 real visitors are in the waitlist table by tonight."
E29_FOCUS_EDIT = "E29 signal today, not polish."
E29_WATCH_EDIT = "E29 watch the funnel drop-off after signup."

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

# Sprint Overview (rev6 redesign): the three headed inline-editable sections. Kickoff
# renders its seeded fields; a Mid-sprint Review sub-field round-trips through the
# shared inlineEdit → per-field PATCH → WS-flush re-render (the same path as Day/ticket).
SO_SPRINT_NAME = "SO sprint"
SO_START = "2026-07-01"           # range contains baseline planning date 2026-07-04
SO_END = "2026-07-14"             # a 2-week span
SO_LIMITING = "SO can build faster than we can validate."
SO_BET = "SO 100 on the waitlist, first cohort activated."
SO_MID_STAND = "SO halfway in, the bet is tracking."


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
        "meta": p.inner_text('[data-approval-block] .proposal-meta'),
        "body": p.inner_text('[data-approval-block] .approval-draft .markdown-block'),
        "claim": p.eval_on_selector_all('[data-marker="running-claim"]', "e=>e.length"),
    }


def _snap_board(p: Page, mid):
    card = f'[data-column="in_progress"] [data-card][data-ticket-id="{mid}"]'
    return {
        "title": p.inner_text(f"{card} .entity-row-title"),
        "pend": p.eval_on_selector_all(f'{card} [data-marker="pending-proposal"]', "e=>e.length"),
        "claim": p.eval_on_selector_all(f'{card} [data-marker="running-claim"]', "e=>e.length"),
    }


def _snap_day(p: Page):
    # The Day overview renders the fake-adapter fields in their slots; the Brief Take
    # body is the state that must survive a reload unchanged.
    return {"take": p.inner_text('[data-day-take-body]')}


def test_e28_day_boundary_accept_all(server, context_factory, open_page, cli, api):
    # A boundary judgment pass FILLS the day's four overview fields (fake adapter,
    # keyed off the planning date). The Day is now the OVERVIEW, not a dashboard: the
    # plan tree, today-ticket list, review-count and chat are gone from it. Each field
    # renders straight into its slot — no markdown parse, no degrade branch.
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
        context_factory(), server, "#/day", "[data-day-overview]", settled=True
    )
    page.wait_for_selector("[data-day-take-body]", timeout=WAIT_MS)

    # The four fields the boundary filled render in their slots.
    assert page.inner_text('[data-day-focus]') == FAKE_FOCUS
    assert page.inner_text('[data-day-take-body]') == FAKE_TAKE
    assert page.inner_text('[data-day-watch-body]') == FAKE_WATCH
    assert page.inner_text('[data-day-lands-body]') == FAKE_LANDS
    # The date orients the read (planning date 2026-07-05). text_content, not
    # inner_text: the date label is text-transform:uppercase, and inner_text would
    # return the rendered "JULY 5" while text_content keeps the raw DOM text.
    assert "July 5" in page.text_content('[data-day-date]')

    # The dropped surfaces have NO Day home anymore (backend endpoints untouched).
    assert page.query_selector(".plan-tree") is None
    assert page.query_selector("[data-review-entry]") is None
    assert page.query_selector("[data-chat-panel]") is None
    assert page.query_selector(".day-ticket-row") is None


def test_e29_day_overview_structured_and_edit(
    server, context_factory, open_page, cli, api
):
    # Seed the four overview fields on the current planning day (baseline 2026-07-04)
    # in one PATCH, then amend two of them in place — a scalar (focus) and a markdown
    # body (watchout) — each editing on its own, no whole-blob re-serialize.
    api.human_patch(
        server,
        f"/api/day/{DAY_PREV}",
        {
            "focus": E29_FOCUS,
            "brief_take": E29_TAKE,
            "watchout": E29_WATCH,
            "if_today_lands": E29_LANDS,
        },
    )

    page = open_page(
        context_factory(), server, "#/day", "[data-day-overview]", settled=True
    )
    page.wait_for_selector("[data-day-take-body]", timeout=WAIT_MS)

    assert page.inner_text('[data-day-focus]') == E29_FOCUS
    assert page.inner_text('[data-day-take-body]') == E29_TAKE
    assert page.inner_text('[data-day-watch-body]') == E29_WATCH
    assert page.inner_text('[data-day-lands-body]') == E29_LANDS
    assert "July 4" in page.text_content('[data-day-date]')  # raw DOM (label uppercases)

    # inlineEdit seeds the raw value on focus and commits on blur → per-field PATCH →
    # the WS flush re-renders. Driven deterministically (focus, overwrite, blur).
    def edit_field(selector, text):
        f0 = page.evaluate("window.__plannerDebug.flushes")
        page.evaluate(
            "(a) => { const el = document.querySelector(a.sel);"
            " el.focus(); el.textContent = a.text; el.blur(); }",
            {"sel": selector, "text": text},
        )
        page.wait_for_function(
            "(f0) => window.__plannerDebug.flushes > f0", arg=f0, timeout=WAIT_MS
        )
        page.wait_for_function(
            "(a) => { const el = document.querySelector(a.sel);"
            " return !!el && el.innerText.trim() === a.text; }",
            arg={"sel": selector, "text": text},
            timeout=WAIT_MS,
        )

    edit_field('[data-day-focus]', E29_FOCUS_EDIT)       # scalar
    edit_field('[data-day-watch-body]', E29_WATCH_EDIT)  # markdown body

    # Both edits landed; the fields nobody touched are unchanged (no re-serialize).
    assert page.inner_text('[data-day-focus]') == E29_FOCUS_EDIT
    assert page.inner_text('[data-day-watch-body]') == E29_WATCH_EDIT
    assert page.inner_text('[data-day-take-body]') == E29_TAKE
    assert page.inner_text('[data-day-lands-body]') == E29_LANDS

    d = api.get(server, "/api/day/today")
    assert d["focus"] == E29_FOCUS_EDIT, d
    assert d["watchout"] == E29_WATCH_EDIT, d
    assert d["brief_take"] == E29_TAKE, d
    assert d["if_today_lands"] == E29_LANDS, d


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
    assert api.get(server, f"/api/tickets/{mid}/runs")["runs"] == []

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

    # Run starts, no reload — the WS flush from run_started re-renders the ticket and
    # the running-claim marker appears in the header. Run history now lives on the API.
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

    # run_closed is a logged event → the claim marker leaves the header (the run is
    # now done, verified above via the CLI close + the API).
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
    mid_t = '[data-approval-block][data-mode="gating-pending"]'
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

    # Day surface — the overview renders the boundary-filled fields; a reload restores.
    ready_d = '[data-day-take-body]'
    page_d = open_page(context_factory(), server, "#/day", ready_d, settled=True)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    assert "July 5" in page_d.text_content('[data-day-date]')  # raw DOM (label uppercases)
    before_d = _snap_day(page_d)
    _reload_settle(page_d, ready_d)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    after_d = _snap_day(page_d)
    expected_d = {"take": FAKE_TAKE}
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


def test_sprint_overview_fields_and_edit(server, context_factory, open_page, api):
    # A current sprint (its 2-week range contains the baseline planning date), seeded
    # with Kickoff content at create → the Overview opens kickoff-open (Kickoff open,
    # Mid-sprint + Sprint Review collapsed until they have content).
    api.human_post(
        server,
        "/api/sprints",
        {
            "name": SO_SPRINT_NAME,
            "date_start": SO_START,
            "date_end": SO_END,
            "limiting_factor": SO_LIMITING,
            "primary_bet": SO_BET,
        },
    )

    ready = '[data-phase="kickoff"]'
    page = open_page(context_factory(), server, "#/sprint/overview", ready, settled=True)

    # The tab pair marks Sprint Overview current; the bet frame echoes primary_bet.
    assert page.inner_text(".tabs .tab.cur") == "Sprint Overview"
    assert SO_BET in page.inner_text(".frame .lead")

    # All three sections render, in the fixed order Kickoff · Mid-sprint · Sprint Review.
    phases = page.eval_on_selector_all(
        "[data-phase]", "els => els.map(e => e.getAttribute('data-phase'))"
    )
    assert phases == ["kickoff", "mid", "review"], phases

    # Kickoff is open (fresh sprint) → its seeded fields render as inline-edit surfaces.
    assert page.inner_text('[data-field="limiting_factor"] .fval') == SO_LIMITING
    assert page.inner_text('[data-field="primary_bet"] .fval') == SO_BET
    # The three Mid-sprint Review sub-fields exist as inline-edit surfaces (empty here).
    for key in ("mid_where_we_stand", "mid_whats_changed", "mid_what_to_adjust"):
        assert page.query_selector(f'[data-field="{key}"] .fval.ed') is not None

    # Inline-edit round-trip on the NEW Mid-sprint field: open its section, focus the
    # field, overwrite, blur → PATCH /api/sprints/{id} {mid_where_we_stand} → the WS
    # flush re-renders from the saved value (no optimistic UI). Same driver as e29.
    page.click('[data-phase="mid"] > summary')
    sel = '[data-field="mid_where_we_stand"] .fval'
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.evaluate(
        "(a) => { const el = document.querySelector(a.sel);"
        " el.focus(); el.textContent = a.text; el.blur(); }",
        {"sel": sel, "text": SO_MID_STAND},
    )
    page.wait_for_function(
        "(f0) => window.__plannerDebug.flushes > f0", arg=f0, timeout=WAIT_MS
    )
    # Mid now has content → the section stays open (running phase) → the value shows.
    page.wait_for_function(
        "(a) => { const el = document.querySelector(a.sel);"
        " return !!el && el.innerText.trim() === a.text; }",
        arg={"sel": sel, "text": SO_MID_STAND},
        timeout=WAIT_MS,
    )

    # The edit persisted canonically, and ONLY that field changed (per-field PATCH).
    cur = api.get(server, "/api/sprint/current")["sprint"]
    assert cur["mid_where_we_stand"] == SO_MID_STAND, cur
    assert cur["mid_whats_changed"] == "", cur
    assert cur["mid_what_to_adjust"] == "", cur
    assert cur["limiting_factor"] == SO_LIMITING, cur   # untouched by the mid edit
