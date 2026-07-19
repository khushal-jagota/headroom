"""E2E flows B — SPEC §18.3 items 28-32 (Playwright, chromium; two contexts in e32).

One test per acceptance item, its name carrying the ``test_eNN_`` anchor the verify
scorer matches: exactly one anchored match per item across the whole e2e suite, so no
parametrize and every shared helper below has a non-``test_`` name. Time is driven
through /api/test/set-now. Assertions use exact values (planning dates, seeded strings,
ticket states); every wait carries an explicit timeout and precedes its assert — no
sleeps."""

from __future__ import annotations

from playwright.sync_api import Page

WAIT_MS = 10_000

# planning-date boundary math (§6.1, dates.py): now-5h → calendar date.
# baseline PLAN_FAKE_NOW = 2026-07-04T12:00  → planning date 2026-07-04 ("yesterday").
# after set-now 2026-07-05T05:01 → now-5h = 2026-07-05T00:01 → planning date 2026-07-05.
NOW_0501 = "2026-07-05T05:01:00"
DAY_PREV = "2026-07-04"
DAY_CUR = "2026-07-05"

# item 28 — without the future rollover agent, the new day's overview is unauthored:
# each structured slot renders empty/placeholder, and no deterministic tick fills it.

# item 29 — the four overview fields, seeded per-field through the human PATCH, then
# amended in place. Each body is a single line so its slot's inner_text is exactly the
# source text.
E29_FOCUS = "E29 ship the waitlist funnel to real traffic."
E29_TAKE = "E29 yesterday closed at sixty-two percent completion."
E29_WATCH = "E29 the hero still does not say what Vylo is in one line."
E29_LANDS = "E29 real visitors are in the waitlist table by tonight."
E29_FOCUS_EDIT = "E29 signal today, not polish."
E29_WATCH_EDIT = "E29 watch the funnel drop-off after signup."
DAY_NOOP_MARKDOWN = "# Day raw forms\n\n* star bullet\n\n1) ordered paren\n\n_line italic_"

# item 30 (ceiling needs_implementation so each of implementation/closeout PARKS
# pending in turn, requiring its own Review approval)
E30_TITLE = "E30 dispatch ticket"
E30_SUCCESS = "E30 success body."
E30_APPROACH = "E30 approach body."
E30_PLAN = "E30 plan body."
E30_IMPLEMENTATION = "E30 implementation body."
E30_CLOSEOUT = "E30 closeout body."

# item 31 (ceiling needs_implementation so the implementation proposal PARKS
# pending → mid-flow)
E31_TITLE = "E31 midflow ticket"
E31_SUCCESS = "E31 success body."
E31_APPROACH = "E31 approach body."
E31_PLAN = "E31 plan body."
E31_IMPLEMENTATION = "E31 implementation proposal body."
E31_DAY_FOCUS = "E31 reload day focus."
E31_DAY_TAKE = "E31 reload day take."
E31_DAY_WATCH = "E31 reload day watch."
E31_DAY_LANDS = "E31 reload day lands."

# item 32
E32_SPRINT_NAME = "E32 sprint"
E32_START = "2026-07-01"  # range contains baseline planning date 2026-07-04
E32_END = "2026-07-12"
E32_ITEM_TITLE = "E32 item"
E32_ITEM_PROJECT = "Vylo"
E32_LOOSE_TITLE = "E32 loose ticket"

# Sprint Overview (rev6 redesign): the three headed inline-editable sections. Kickoff
# renders its seeded fields; a Mid-sprint Review sub-field round-trips through the
# shared inlineEdit → per-field PATCH → WS-flush re-render (the same path as Day/ticket).
SO_SPRINT_NAME = "SO sprint"
SO_START = "2026-07-01"  # range contains baseline planning date 2026-07-04
SO_END = "2026-07-14"  # a 2-week span
SO_LIMITING = "SO can build faster than we can validate."
SO_BET = "SO 100 on the waitlist, first cohort activated."
SO_MID_STAND = "SO halfway in, the bet is tracking."


def _set_now(api, server, iso):
    return api.direct_post(server, "/api/test/set-now", {"now": iso})


def _scope_and_advance(server, api, cli, tid, ceiling, bodies):
    # Unattributed direct scope; attributed worker agents are rejected.
    g = api.direct_post(
        server, f"/api/tickets/{tid}/scope", {"ceiling": ceiling, "at_cap": "propose"}
    )
    assert g["ceiling"] == ceiling and g["at_cap"] == "propose", g
    # Claimless CLI proposals auto-accept up the chain to needs_implementation (like flows_a e27).
    for field in ("success", "approach", "plan"):
        cli(
            server,
            "worker",
            "propose",
            "--body-file",
            "-",
            "--recap",
            f"{field} ready.",
            ticket_id=tid,
            stdin=bodies[field],
        )
    d = api.get(server, f"/api/tickets/{tid}")
    assert d["stage"] == "needs_implementation", d
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
        "stage": p.get_attribute('section[data-screen="ticket"]', "data-stage"),
        "mode": p.get_attribute("[data-approval-block]", "data-mode"),
        "field": p.get_attribute("[data-approval-block]", "data-field"),
        "body": p.inner_text("[data-approval-block] .approval-draft"),
    }


def _snap_board(p: Page, mid):
    card = f'[data-card][data-ticket-stage="needs_implementation"][data-ticket-id="{mid}"]'
    stage = '[data-worker-type="coding"] [data-stage-key="needs_implementation"]'
    return {
        "title": p.inner_text(f"{card} .list-row-title"),
        "stage": p.inner_text(f"{stage} > summary .board-workspace-stage-label"),
        "nested": p.eval_on_selector_all(f"{stage} {card}", "e=>e.length"),
        "marks": p.eval_on_selector_all(f"{card} .board-workspace-stage-mark", "e=>e.length"),
    }


def _snap_day(p: Page):
    # The Day overview renders each structured field in its own slot; the Brief Take
    # body is the state that must survive a reload unchanged.
    return {"take": p.inner_text("[data-day-take-body]")}


def test_e28_day_overview_empty_until_rollover_agent(server, context_factory, open_page, cli, api):
    # The Day is the OVERVIEW, not a dashboard: the plan tree, today-ticket list,
    # review-count and chat are gone from it. With deterministic boundary removed,
    # crossing to the new planning date leaves the structured overview unauthored until
    # a human or future rollover agent writes it.
    r = _set_now(api, server, NOW_0501)
    assert r["planning_date"] == DAY_CUR, r

    page = open_page(context_factory(), server, "#/day", "[data-day-overview]", settled=True)
    page.wait_for_selector("[data-day-take-body]", timeout=WAIT_MS)

    # The four fields render in their slots, but stay empty by default.
    assert page.text_content("[data-day-focus]") == ""
    assert page.text_content("[data-day-take-body]").strip() == ""
    assert page.text_content("[data-day-watch-body]").strip() == ""
    assert page.text_content("[data-day-lands-body]").strip() == ""
    d = api.get(server, "/api/day/today")
    assert d["id"] == f"day_{DAY_CUR}", d
    assert d["focus"] == d["brief_take"] == d["watchout"] == d["if_today_lands"] == ""
    # The date orients the read (planning date 2026-07-05). text_content, not
    # inner_text: the date label is text-transform:uppercase, and inner_text would
    # return the rendered "JUL 5" while text_content keeps the raw DOM text. The
    # redesign speaks the short month form (Jul, not July).
    assert "Jul 5" in page.text_content("[data-day-date]")

    # The dropped surfaces have NO Day home anymore (backend endpoints untouched).
    assert page.query_selector(".plan-tree") is None
    assert page.query_selector("[data-review-entry]") is None
    assert page.query_selector("[data-chat-panel]") is None
    # The Day overview is not a today-ticket list: no per-ticket rows render on it.
    assert page.query_selector("[data-day-overview] [data-ticket-id]") is None


def test_e29_day_overview_structured_and_edit(server, context_factory, open_page, cli, api):
    # Seed the four overview fields on the current planning day (baseline 2026-07-04)
    # in one PATCH, then amend two of them in place — a scalar (focus) and a markdown
    # body (watchout) — each editing on its own, no whole-blob re-serialize.
    api.direct_patch(
        server,
        f"/api/day/{DAY_PREV}",
        {
            "focus": E29_FOCUS,
            "brief_take": E29_TAKE,
            "watchout": E29_WATCH,
            "if_today_lands": E29_LANDS,
        },
    )

    page = open_page(context_factory(), server, "#/day", "[data-day-overview]", settled=True)
    page.wait_for_selector("[data-day-take-body]", timeout=WAIT_MS)

    assert page.inner_text("[data-day-focus]") == E29_FOCUS
    assert page.inner_text("[data-day-take-body]") == E29_TAKE
    assert page.inner_text("[data-day-watch-body]") == E29_WATCH
    assert page.inner_text("[data-day-lands-body]") == E29_LANDS
    assert "Jul 4" in page.text_content("[data-day-date]")  # raw DOM (label uppercases)

    # InlineEdit remains one contenteditable surface and commits real edits on blur.
    def edit_field(selector, text):
        f0 = page.evaluate("window.__plannerDebug.flushes")
        page.evaluate(
            "(a) => { const el = document.querySelector(a.sel);"
            " el.focus(); el.textContent = a.text;"
            " el.dispatchEvent(new InputEvent('input', "
            "{ bubbles: true, inputType: 'insertText', data: a.text }));"
            " el.blur(); }",
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

    edit_field("[data-day-focus]", E29_FOCUS_EDIT)  # scalar
    edit_field("[data-day-watch-body]", E29_WATCH_EDIT)  # markdown body

    # Both edits landed; the fields nobody touched are unchanged (no re-serialize).
    assert page.inner_text("[data-day-focus]") == E29_FOCUS_EDIT
    assert page.inner_text("[data-day-watch-body]") == E29_WATCH_EDIT
    assert page.inner_text("[data-day-take-body]") == E29_TAKE
    assert page.inner_text("[data-day-lands-body]") == E29_LANDS

    d = api.get(server, "/api/day/today")
    assert d["focus"] == E29_FOCUS_EDIT, d
    assert d["watchout"] == E29_WATCH_EDIT, d
    assert d["brief_take"] == E29_TAKE, d
    assert d["if_today_lands"] == E29_LANDS, d


def test_day_markdown_focus_noop_keeps_raw_source(server, context_factory, open_page, api):
    api.direct_patch(
        server,
        f"/api/day/{DAY_PREV}",
        {"watchout": DAY_NOOP_MARKDOWN},
    )

    page = open_page(context_factory(), server, "#/day", "[data-day-overview]", settled=True)
    page.wait_for_selector("[data-day-watch-body] h1", timeout=WAIT_MS)

    assert page.inner_text("[data-day-watch-body] h1") == "Day raw forms"
    assert page.eval_on_selector_all(
        "[data-day-watch-body] ul li", "els => els.map(e => e.textContent)"
    ) == ["star bullet"]
    assert page.eval_on_selector_all(
        "[data-day-watch-body] ol li", "els => els.map(e => e.textContent)"
    ) == ["ordered paren"]

    page.focus("[data-day-watch-body]")
    assert page.locator("[data-day-watch-body]").get_attribute("contenteditable") == "true"
    assert page.locator("[data-day-watch-body] [data-markdown-edit]").count() == 0
    assert page.locator("[data-day-watch-body] [data-markdown-source-editor]").count() == 0
    page.locator("[data-day-watch-body]").blur()

    d = api.get(server, "/api/day/today")
    assert d["watchout"] == DAY_NOOP_MARKDOWN, d


def test_e30_review_approve_to_done(server, context_factory, open_page, cli, api):
    # A ticket advanced to needs_implementation through claimless CLI proposals (the
    # agent's normal path now — no dispatcher, no claim). Ceiling needs_implementation
    # means the implementation proposal PARKS pending; approving it via Review defaults
    # the onward scope to the next stage (needs_closeout), where closeout PARKS pending
    # in turn until its own Review approval reaches done.
    mid = cli(server, "ticket", "create", "--worker-type", "coding", "--title", E30_TITLE)["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": mid})
    _scope_and_advance(
        server,
        api,
        cli,
        mid,
        "needs_implementation",
        {"success": E30_SUCCESS, "approach": E30_APPROACH, "plan": E30_PLAN},
    )

    ready = f'section[data-screen="ticket"][data-ticket-id="{mid}"]'
    page = open_page(context_factory(), server, f"#/ticket/{mid}", ready, settled=True)
    assert (
        page.get_attribute('section[data-screen="ticket"]', "data-stage")
        == "needs_implementation"
    )
    assert page.query_selector('[data-marker="agent-running-step"]') is None

    # Worker files implementation claimless; ceiling needs_implementation ⇒ it PARKS
    # pending (nothing auto-accepts past the ceiling).
    r = cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Implementation ready.",
        ticket_id=mid,
        stdin=E30_IMPLEMENTATION,
    )
    assert r["stage"] == "needs_implementation", r
    assert r["fields"]["implementation"]["proposal"]["body"] == E30_IMPLEMENTATION, r

    # Ticket page shows the pending gate without reload.
    page.wait_for_selector(
        '[data-field="implementation"] [data-approval-block][data-mode="gating-pending"]',
        timeout=WAIT_MS,
    )

    # Review decision + approve via the Review card, ordinary field path.
    card = f'[data-review-card][data-ticket-id="{mid}"]'
    rpage = open_page(context_factory(), server, "#/review", card, settled=True)
    assert rpage.get_attribute(card, "data-field") == "implementation"
    decisions = api.get(server, "/api/review")["ticket_decisions"]
    assert len(decisions) == 1, decisions
    assert decisions[0]["ticket_id"] == mid, decisions
    assert decisions[0]["field"] == "implementation", decisions

    assert rpage.locator(f"{card} [data-scope-ceiling]").input_value() == "needs_closeout"
    rpage.click(f"{card} [data-accept]")
    rpage.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)
    assert api.get(server, f"/api/tickets/{mid}")["stage"] == "needs_closeout"

    # Ticket page flips without reload.
    page.wait_for_function(
        "() => { const s = document.querySelector('section[data-screen=\"ticket\"]');"
        " return !!s && s.getAttribute('data-stage') === 'needs_closeout'; }",
        timeout=WAIT_MS,
    )

    # Worker files closeout claimless; ceiling carried forward from the implementation
    # approval (needs_closeout) ⇒ it PARKS pending too.
    r2 = cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Closeout ready.",
        ticket_id=mid,
        stdin=E30_CLOSEOUT,
    )
    assert r2["stage"] == "needs_closeout", r2
    assert r2["fields"]["closeout"]["proposal"]["body"] == E30_CLOSEOUT, r2

    card2 = f'[data-review-card][data-ticket-id="{mid}"]'
    rpage2 = open_page(context_factory(), server, "#/review", card2, settled=True)
    assert rpage2.get_attribute(card2, "data-field") == "closeout"
    assert rpage2.locator(f"{card2} [data-scope-ceiling]").input_value() == "done"
    rpage2.click(f"{card2} [data-accept]")
    rpage2.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)
    assert api.get(server, f"/api/tickets/{mid}")["stage"] == "done"

    # Ticket page flips to done, rendering exactly the six ordered stages.
    page.wait_for_function(
        "() => { const s = document.querySelector('section[data-screen=\"ticket\"]');"
        " return !!s && s.getAttribute('data-stage') === 'done'; }",
        timeout=WAIT_MS,
    )
    fields_order = page.eval_on_selector_all(
        ".fields [data-field]", "els => els.map(e => e.getAttribute('data-field'))"
    )
    assert fields_order == [
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    ], fields_order


def test_e31_refresh_restores_state(server, context_factory, open_page, cli, api):
    mid = cli(server, "ticket", "create", "--worker-type", "coding", "--title", E31_TITLE)["id"]
    _scope_and_advance(
        server,
        api,
        cli,
        mid,
        "needs_implementation",
        {"success": E31_SUCCESS, "approach": E31_APPROACH, "plan": E31_PLAN},
    )

    # Human-author a day overview on the next planning date (for the third
    # reload-restore surface below).
    _set_now(api, server, NOW_0501)
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": mid})
    api.direct_patch(
        server,
        f"/api/day/{DAY_CUR}",
        {
            "focus": E31_DAY_FOCUS,
            "brief_take": E31_DAY_TAKE,
            "watchout": E31_DAY_WATCH,
            "if_today_lands": E31_DAY_LANDS,
        },
    )

    # Worker files implementation claimless; ceiling needs_implementation ⇒ it PARKS
    # pending (nothing auto-accepts past the ceiling), leaving a gating-pending
    # proposal to reload-restore.
    r = cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Implementation proposed.",
        ticket_id=mid,
        stdin=E31_IMPLEMENTATION,
    )
    assert r["stage"] == "needs_implementation", r
    assert r["fields"]["implementation"]["proposal"]["body"] == E31_IMPLEMENTATION, r

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
        "stage": "needs_implementation",
        "mode": "gating-pending",
        "field": "implementation",
        "body": E31_IMPLEMENTATION,
    }
    assert before_t == after_t == expected_t, (before_t, after_t)

    # Board surface.
    ready_b = 'section[data-screen="workspace"]'
    mid_b = f'[data-card][data-ticket-stage="needs_implementation"][data-ticket-id="{mid}"]'
    page_b = open_page(context_factory(), server, "#/workspace", ready_b, settled=True)
    page_b.wait_for_selector(mid_b, timeout=WAIT_MS)
    before_b = _snap_board(page_b, mid)
    _reload_settle(page_b, ready_b)
    page_b.wait_for_selector(mid_b, timeout=WAIT_MS)
    after_b = _snap_board(page_b, mid)
    expected_b = {"title": E31_TITLE, "stage": "Implementation", "nested": 1, "marks": 0}
    assert before_b == after_b == expected_b, (before_b, after_b)

    # Day surface — the overview renders structured fields; a reload restores.
    ready_d = "[data-day-take-body]"
    page_d = open_page(context_factory(), server, "#/day", ready_d, settled=True)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    assert "Jul 5" in page_d.text_content("[data-day-date]")  # raw DOM (label uppercases)
    before_d = _snap_day(page_d)
    _reload_settle(page_d, ready_d)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    after_d = _snap_day(page_d)
    expected_d = {"take": E31_DAY_TAKE}
    assert before_d == after_d == expected_d, (before_d, after_d)


def test_e32_sprint_live_status_and_loose(server, context_factory, open_page, cli, api):
    s = api.direct_post(
        server,
        "/api/sprints",
        {"name": E32_SPRINT_NAME, "date_start": E32_START, "date_end": E32_END},
    )
    sid = s["id"]

    iid = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        E32_ITEM_TITLE,
        "--project",
        E32_ITEM_PROJECT,
        "--sprint",
        sid,
    )["id"]
    ltid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        E32_LOOSE_TITLE,
        "--sprint",
        sid,
    )["id"]

    # Status groups were replaced by project groups; the item's status now lives on
    # the row itself as data-item-status. Assert the item is present exactly once and
    # carries the todo status, and that the loose ticket sits under the loose group.
    ready = f'[data-item-id="{iid}"][data-item-status="todo"]'
    pa = open_page(context_factory(), server, "#/sprint", ready, settled=True)
    pb = open_page(context_factory(), server, "#/sprint", ready, settled=True)

    for p in (pa, pb):
        p.wait_for_selector(ready, timeout=WAIT_MS)
        assert p.locator(f'[data-item-id="{iid}"]').count() == 1
        assert p.get_attribute(f'[data-item-id="{iid}"]', "data-item-status") == "todo"
        assert p.query_selector(f'[data-loose] [data-ticket-id="{ltid}"]') is not None

    fa = pa.evaluate("window.__plannerDebug.flushes")
    fb = pb.evaluate("window.__plannerDebug.flushes")

    child = cli(
        server,
        "ticket",
        "create", "--worker-type", "coding",
        "--title",
        f"{E32_ITEM_TITLE} child",
        "--sprint-item",
        iid,
    )["id"]
    _scope_and_advance(
        server,
        api,
        cli,
        child,
        "needs_implementation",
        {
            "success": "E32 success",
            "approach": "E32 approach",
            "plan": "E32 plan",
        },
    )

    for p, f0 in ((pa, fa), (pb, fb)):
        # The same item now reads in_progress via its status attribute; it is still a
        # single element (moved status, not duplicated across groups).
        p.wait_for_selector(
            f'[data-item-id="{iid}"][data-item-status="in_progress"]', timeout=WAIT_MS
        )
        assert p.locator(f'[data-item-id="{iid}"]').count() == 1
        assert p.get_attribute(f'[data-item-id="{iid}"]', "data-item-status") == "in_progress"
        assert p.query_selector(f'[data-ticket-id="{child}"]') is not None
        assert p.evaluate("window.__plannerDebug.flushes") > f0

    cur = api.get(server, "/api/sprint/current")
    assert iid in [i["id"] for i in cur["groups"]["in_progress"]], cur
    assert iid not in [i["id"] for i in cur["groups"]["todo"]], cur
    assert ltid in [t["id"] for t in cur["loose_tickets"]], cur

    # The item ticket projection carries the two board-card signals the sprint ticket
    # rows colour off (added this wave): has_pending_proposal + ticket_status. The
    # advanced child sits in_progress with no pending gating proposal.
    item = next(i for i in cur["groups"]["in_progress"] if i["id"] == iid)
    child_row = next(t for t in item["tickets"] if t["id"] == child)
    assert child_row["has_pending_proposal"] is False, child_row
    assert child_row["ticket_status"] == "empty", child_row


def test_sprint_overview_fields_and_edit(server, context_factory, open_page, api):
    # A current sprint (its 2-week range contains the baseline planning date), seeded
    # with Kickoff content at create → the Overview opens kickoff-open (Kickoff open,
    # Mid-sprint + Sprint Review collapsed until they have content).
    api.direct_post(
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
    # Legacy #/sprint/overview replace-redirects to the new documents page.
    page = open_page(context_factory(), server, "#/sprint/overview", ready, settled=True)

    # The redirect landed on the documents page (tabs are gone): the hash is
    # #/sprint/documents and the page shows its unique "Sprint documents" heading.
    assert page.url.endswith("#/sprint/documents")
    assert page.inner_text(".sprint-docs-title") == "Sprint documents"

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
        assert page.query_selector(f'[data-field="{key}"] .fval .ed') is not None

    # Inline-edit round-trip on the NEW Mid-sprint field: open its section, edit
    # the contenteditable surface, blur → PATCH /api/sprints/{id}
    # {mid_where_we_stand} → the WS flush re-renders from the saved value.
    page.click('[data-phase="mid"] > summary')
    sel = '[data-field="mid_where_we_stand"] .fval .ed'
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.evaluate(
        "(a) => { const el = document.querySelector(a.sel);"
        " el.focus(); el.textContent = a.text;"
        " el.dispatchEvent(new InputEvent('input', "
        "{ bubbles: true, inputType: 'insertText', data: a.text }));"
        " el.blur(); }",
        {"sel": sel, "text": SO_MID_STAND},
    )
    page.wait_for_function("(f0) => window.__plannerDebug.flushes > f0", arg=f0, timeout=WAIT_MS)
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
    assert cur["limiting_factor"] == SO_LIMITING, cur  # untouched by the mid edit
