"""E2E flows B — SPEC §18.3 items 28-32 (Playwright, chromium; two contexts in e32).

One test per acceptance item, its name carrying the ``test_eNN_`` anchor the verify
scorer matches: exactly one anchored match per item across the whole e2e suite, so no
parametrize and every shared helper below has a non-``test_`` name. Time is driven
through /api/test/set-now. Assertions use exact values (planning dates, seeded strings,
ticket states); every wait carries an explicit timeout and precedes its assert — no
sleeps."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

WAIT_MS = 10_000

# planning-date boundary math (§6.1, dates.py): now-5h → calendar date.
# baseline PLAN_FAKE_NOW = 2026-07-04T12:00  → planning date 2026-07-04 ("yesterday").
# after set-now 2026-07-05T05:01 → now-5h = 2026-07-05T00:01 → planning date 2026-07-05.
NOW_0501 = "2026-07-05T05:01:00"
DAY_PREV = "2026-07-04"
DAY_CUR = "2026-07-05"

# item 28 — without the future rollover agent, the new day's overview is unauthored:
# each structured slot renders empty/placeholder, and no deterministic tick fills it.

# item 29 — the four overview fields, seeded through the human PATCH and rendered as a
# read-only Day summary. Each body is a single line for exact browser assertions.
E29_FOCUS = "E29 ship the waitlist funnel to real traffic."
E29_TAKE = "E29 yesterday closed at sixty-two percent completion."
E29_WATCH = "E29 the hero still does not say what Vylo is in one line."
E29_LANDS = "E29 real visitors are in the waitlist table by tonight."
E29_MIDDAY = "E29 signups are live; the remaining risk is activation."
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
E32_FALLBACK_TITLE = "E32 fallback ticket"


def _set_now(api: ApiHelper, server: ServerHandle, iso: str) -> JsonObject:
    return api.direct_post(server, "/api/test/set-now", {"now": iso})


def _scope_and_advance(
    server: ServerHandle,
    api: ApiHelper,
    cli: Callable[..., JsonObject],
    tid: str,
    ceiling: str,
    bodies: dict[str, str],
) -> JsonObject:
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


def _reload_settle(page: Page, ready_selector: str) -> None:
    # open_page opens FRESH pages; after page.reload() the __plannerDebug counters
    # reset — re-apply the same gate the fixture applies.
    page.reload()
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    page.wait_for_function(
        "() => window.__plannerDebug && window.__plannerDebug.sseOpens >= 1",
        timeout=WAIT_MS,
    )


def _snap_ticket(p: Page) -> dict[str, str | None]:
    return {
        "stage": p.get_attribute('section[data-screen="ticket"]', "data-stage"),
        "mode": p.get_attribute("[data-approval-block]", "data-mode"),
        "field": p.get_attribute("[data-approval-block]", "data-field"),
        "body": p.inner_text("[data-approval-block] .approval-draft"),
    }


def _snap_board(p: Page, mid: str) -> dict[str, Any]:
    card = (
        f'[data-card][data-ticket-stage="needs_implementation"][data-ticket-id="{mid}"]'
    )
    bucket = '[data-bucket-section][data-bucket-key="awaiting_approval"]'
    return {
        "title": p.inner_text(f"{card} .list-row-title"),
        "bucket": p.inner_text(f"{bucket} > summary .board-workspace-bucket-label"),
        "nested": p.eval_on_selector_all(f"{bucket} {card}", "e=>e.length"),
        "marks": p.eval_on_selector_all(
            f"{card} .board-workspace-stage-mark", "e=>e.length"
        ),
        "agent_working": p.get_attribute(
            f"{card} .board-workspace-stage-mark", "data-agent-working"
        ),
    }


def _snap_day(p: Page) -> dict[str, str]:
    return {
        "focus": p.inner_text("[data-day-focus]"),
        "take": p.inner_text("[data-day-brief-take]"),
        "watch": p.inner_text("[data-day-watch] .v"),
        "lands": p.inner_text("[data-day-lands]"),
    }


def test_e29_day_overview_structured_and_read_only(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    # Seed the four overview fields on the current planning day (baseline 2026-07-04).
    api.direct_patch(
        server,
        f"/api/day/{DAY_PREV}",
        {
            "focus": E29_FOCUS,
            "brief_take": E29_TAKE,
            "watchout": E29_WATCH,
            "if_today_lands": E29_LANDS,
            "midday_reconciliation": E29_MIDDAY,
        },
    )
    day_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "E29 Day overview ticket",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": day_ticket})

    page = open_page(context_factory(), server, "#/day", "[data-day-overview]")
    page.wait_for_selector("[data-day-brief-take]", timeout=WAIT_MS)

    assert page.inner_text("[data-day-focus]") == E29_FOCUS
    assert page.inner_text("[data-day-brief-take]") == E29_TAKE
    assert page.inner_text("[data-day-watch] .v") == E29_WATCH
    assert page.inner_text("[data-day-lands]") == f"If today lands — {E29_LANDS}"
    assert page.locator("[data-day-midday-body]").count() == 0
    assert "Midday reconciliation" not in page.locator('[data-screen="day"]').inner_text()
    e29_date_text = page.text_content("[data-day-date]")  # raw DOM (label uppercases)
    assert e29_date_text is not None
    assert "Jul 4" in e29_date_text

    # A server update remains visible after a reload. Day itself offers no inline edit.
    api.direct_patch(
        server,
        f"/api/day/{DAY_PREV}",
        {
            "focus": "E29 signal today, not polish.",
            "watchout": "E29 watch the funnel drop-off after signup.",
        },
    )
    _reload_settle(page, "[data-day-overview]")
    page.wait_for_selector("[data-day-brief-take]", timeout=WAIT_MS)
    assert page.inner_text("[data-day-focus]") == "E29 signal today, not polish."
    assert page.inner_text("[data-day-watch] .v") == "E29 watch the funnel drop-off after signup."
    assert page.inner_text("[data-day-brief-take]") == E29_TAKE
    assert page.inner_text("[data-day-lands]") == f"If today lands — {E29_LANDS}"

    d = api.get(server, "/api/day/today")
    assert d["focus"] == "E29 signal today, not polish.", d
    assert d["watchout"] == "E29 watch the funnel drop-off after signup.", d
    assert d["brief_take"] == E29_TAKE, d
    assert d["if_today_lands"] == E29_LANDS, d
    assert d["midday_reconciliation"] == E29_MIDDAY, d


def test_e30_review_approve_to_done(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    # A ticket advanced to needs_implementation through claimless CLI proposals (the
    # agent's normal path now — no dispatcher, no claim). Ceiling needs_implementation
    # means the implementation proposal PARKS pending; approving it via Review defaults
    # the onward scope to the next stage (needs_closeout), where closeout PARKS pending
    # in turn until its own Review approval reaches done.
    mid = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", E30_TITLE
    )["id"]
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
    page = open_page(context_factory(), server, f"#/ticket/{mid}", ready)
    assert (
        page.get_attribute('section[data-screen="ticket"]', "data-stage")
        == "needs_implementation"
    )
    assert page.query_selector('[data-marker="agent"]') is None

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
    rpage = open_page(context_factory(), server, "#/review", card)
    assert rpage.get_attribute(card, "data-field") == "implementation"
    decisions = api.get(server, "/api/review")["items"]
    assert len(decisions) == 1, decisions
    assert decisions[0]["ticket_id"] == mid, decisions
    assert decisions[0]["field"] == "implementation", decisions

    assert (
        rpage.locator(f"{card} [data-scope-ceiling]").input_value() == "needs_closeout"
    )
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
    rpage2 = open_page(context_factory(), server, "#/review", card2)
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


def test_e31_refresh_restores_state(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    mid = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", E31_TITLE
    )["id"]
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
    page_t = open_page(context_factory(), server, f"#/ticket/{mid}", ready_t)
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
    mid_b = (
        f'[data-card][data-ticket-stage="needs_implementation"][data-ticket-id="{mid}"]'
    )
    page_b = open_page(context_factory(), server, "#/workspace", ready_b)
    page_b.wait_for_selector(mid_b, timeout=WAIT_MS)
    before_b = _snap_board(page_b, mid)
    _reload_settle(page_b, ready_b)
    page_b.wait_for_selector(mid_b, timeout=WAIT_MS)
    after_b = _snap_board(page_b, mid)
    expected_b = {
        "title": E31_TITLE,
        "bucket": "Awaiting approval",
        "nested": 1,
        "marks": 1,
        "agent_working": "false",
    }
    assert before_b == after_b == expected_b, (before_b, after_b)

    # Day surface — the overview renders structured fields; a reload restores.
    ready_d = "[data-day-brief-take]"
    page_d = open_page(context_factory(), server, "#/day", ready_d)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    e31_date_text = page_d.text_content("[data-day-date]")  # raw DOM (label uppercases)
    assert e31_date_text is not None
    assert "Jul 5" in e31_date_text
    before_d = _snap_day(page_d)
    _reload_settle(page_d, ready_d)
    page_d.wait_for_selector(ready_d, timeout=WAIT_MS)
    after_d = _snap_day(page_d)
    expected_d = {
        "focus": E31_DAY_FOCUS,
        "take": E31_DAY_TAKE,
        "watch": E31_DAY_WATCH,
        "lands": f"If today lands — {E31_DAY_LANDS}",
    }
    assert before_d == after_d == expected_d, (before_d, after_d)


def test_e32_sprint_live_status_and_fallback(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
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
        "--priority",
        "P2",
    )["id"]
    fallback_ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        E32_FALLBACK_TITLE,
        "--project",
        E32_ITEM_PROJECT,
        "--priority",
        "P1",
    )["id"]

    # Status groups were replaced by project groups; the item's status now lives on
    # the row itself as data-item-status. Omitted placement resolves the current
    # sprint's Other item, which remains a visible fallback rather than a loose group.
    current_before = api.get(server, "/api/sprint/current")
    fallback_item = next(
        item
        for group in current_before["groups"].values()
        for item in group
        if item["kind"] == "other"
        and any(ticket["id"] == fallback_ticket_id for ticket in item["tickets"])
    )
    fallback_item_id = fallback_item["id"]

    ready = f'[data-item-id="{iid}"][data-item-status="todo"]'
    pa = open_page(context_factory(), server, "#/sprint", ready)
    pb = open_page(context_factory(), server, "#/sprint", ready)

    for p in (pa, pb):
        p.wait_for_selector(ready, timeout=WAIT_MS)
        assert p.locator(f'[data-item-id="{iid}"]').count() == 1
        p.locator(f'[data-item-id="{iid}"]').evaluate(
            "(element) => { element.open = true; }"
        )
        assert (
            p.locator(f'[data-item-id="{iid}"] [data-priority-tile="P2"]').count()
            == 1
        )
        assert p.get_attribute(f'[data-item-id="{iid}"]', "data-item-status") == "todo"
        assert (
            p.get_attribute(f'[data-item-id="{fallback_item_id}"]', "data-item-kind")
            == "other"
        )
        assert (
            p.query_selector(
                f'[data-item-id="{fallback_item_id}"] [data-ticket-id="{fallback_ticket_id}"]'
            )
            is not None
        )
        p.locator(f'[data-item-id="{fallback_item_id}"]').evaluate(
            "(element) => { element.open = true; }"
        )
        assert (
            p.locator(
                f'[data-item-id="{fallback_item_id}"] [data-ticket-id="{fallback_ticket_id}"] '
                '[data-priority-tile="P1"]'
            ).count()
            == 1
        )
        assert "fallback" in p.inner_text(
            f'[data-item-id="{fallback_item_id}"] summary'
        )
        assert p.query_selector("[data-loose]") is None

    # The Ticket header now keeps only priority, project, and worker identity.
    ticket_page = open_page(
        context_factory(),
        server,
        f"#/ticket/{fallback_ticket_id}",
        "[data-ticket-identity]",
    )
    assert ticket_page.locator("[data-sprint-item-control]").count() == 0
    assert ticket_page.locator("[data-deadline-control]").count() == 0

    fa = pa.evaluate("window.__plannerDebug.flushes")
    fb = pb.evaluate("window.__plannerDebug.flushes")

    child = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
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
        assert (
            p.get_attribute(f'[data-item-id="{iid}"]', "data-item-status")
            == "in_progress"
        )
        assert p.query_selector(f'[data-ticket-id="{child}"]') is not None
        assert p.evaluate("window.__plannerDebug.flushes") > f0

    cur = api.get(server, "/api/sprint/current")
    assert iid in [i["id"] for i in cur["groups"]["in_progress"]], cur
    assert iid not in [i["id"] for i in cur["groups"]["todo"]], cur
    assert "loose_tickets" not in cur, cur

    # The item ticket projection carries the two board-card signals the sprint ticket
    # rows colour off (added this wave): has_pending_proposal + ticket_status. The
    # advanced child sits in_progress with no pending gating proposal.
    item = next(i for i in cur["groups"]["in_progress"] if i["id"] == iid)
    child_row = next(t for t in item["tickets"] if t["id"] == child)
    assert child_row["has_pending_proposal"] is False, child_row
    assert child_row["ticket_status"] == "empty", child_row
