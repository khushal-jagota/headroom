"""Browser coverage for derived blocker placement, detail, and removal."""

from __future__ import annotations

import sqlite3

import httpx

WAIT_MS = 10_000


def _post_stage(server, ticket_id: str, stage: str) -> dict:
    resp = httpx.post(
        f"{server.base}/api/tickets/{ticket_id}/stage",
        json={"to_stage": stage},
        timeout=10.0,
    )
    assert resp.status_code < 300, f"POST Stage -> {resp.status_code}: {resp.text}"
    return resp.json()


def _get_ticket(server, ticket_id: str) -> dict:
    resp = httpx.get(f"{server.base}/api/tickets/{ticket_id}", timeout=10.0)
    assert resp.status_code < 300, f"GET ticket -> {resp.status_code}: {resp.text}"
    return resp.json()


def test_workspace_places_post_kickoff_dependents_in_quiet_blocked_section(
    server, context_factory, open_page, cli, api
) -> None:
    blocker = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Prerequisite"
    )["id"]
    kickoff_dependent = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Kickoff dependent",
        "--kickoff-note",
        "Pending kickoff",
        "--blocked-by",
        blocker,
    )["id"]
    later_dependent = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Later dependent",
        "--blocked-by",
        blocker,
    )["id"]
    shared_dependent = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Shared dependent",
        "--blocked-by",
        blocker,
    )["id"]
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET stage = 'needs_plan', ticket_status = 'awaiting_approval' "
            "WHERE id = ?",
            (later_dependent,),
        )
        conn.execute(
            "UPDATE tickets SET stage = 'needs_approach', ticket_status = 'empty' WHERE id = ?",
            (shared_dependent,),
        )
    for ticket_id in (kickoff_dependent, later_dependent, shared_dependent):
        api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        f'[data-card][data-ticket-id="{later_dependent}"]',
        settled=True,
    )
    coding = '[data-worker-type="coding"]'
    blocked = f'{coding} [data-stage-key="blocked"]'
    kickoff = f'{coding} [data-stage-key="needs_kickoff"]'
    later_card = f'[data-card][data-ticket-id="{later_dependent}"]'
    kickoff_card = f'[data-card][data-ticket-id="{kickoff_dependent}"]'
    shared_card = f'[data-card][data-ticket-id="{shared_dependent}"]'

    assert page.locator(f"{blocked} {later_card}").count() == 1
    assert page.locator(f"{blocked} {shared_card}").count() == 1
    assert page.locator(f"{kickoff} {kickoff_card}").count() == 1
    assert page.locator(f"{blocked} {kickoff_card}").count() == 0
    assert page.get_attribute(later_card, "data-ticket-stage") == "needs_plan"
    assert page.get_attribute(
        f"{later_card} .board-workspace-stage-mark", "data-workspace-dot-state"
    ) == "quiet"
    assert "Prerequisite" not in page.inner_text(f"{blocked} > .disclosure-body")

    cli(server, "ticket", "unblock", later_dependent, "--by", blocker)
    page.wait_for_selector(f'{coding} [data-stage-key="needs_plan"] {later_card}', timeout=WAIT_MS)
    assert page.locator(f"{blocked} {shared_card}").count() == 1

    _post_stage(server, blocker, "done")
    page.wait_for_selector(
        f'{coding} [data-stage-key="needs_approach"] {shared_card}', timeout=WAIT_MS
    )
    assert page.locator(blocked).count() == 0
    assert _get_ticket(server, later_dependent)["stage"] == "needs_plan"
    assert _get_ticket(server, shared_dependent)["stage"] == "needs_approach"


def test_ticket_detail_shows_only_active_direct_blockers_and_removes_each_link(
    server, context_factory, open_page, cli
) -> None:
    active_blocker = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Active blocker",
    )["id"]
    cleared_blocker = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Cleared blocker",
    )["id"]
    shared_blocker = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Shared blocker",
    )["id"]
    blocked_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Blocked ticket",
        "--kickoff-note",
        "Still awaiting kickoff",
        "--blocked-by",
        active_blocker,
        "--blocked-by",
        cleared_blocker,
        "--blocked-by",
        shared_blocker,
    )["id"]
    assert _get_ticket(server, cleared_blocker)["stage"] == "needs_success"
    _post_stage(server, cleared_blocker, "done")

    ready = f'section[data-screen="ticket"][data-ticket-id="{blocked_ticket}"]'
    page = open_page(context_factory(), server, f"#/ticket/{blocked_ticket}", ready, settled=True)

    summary = page.locator("[data-blocker-summary]")
    summary.wait_for(timeout=WAIT_MS)
    assert summary.locator('[data-blocker-group="blocked-by"]').inner_text() == (
        "BLOCKED BY\nActive blocker\nRemove\nShared blocker\nRemove"
    )
    assert summary.locator('[data-blocker-group="blocks"]').count() == 0
    assert "Cleared blocker" not in summary.inner_text()
    assert summary.locator(f'a[href="#/ticket/{active_blocker}"]').count() == 1
    assert summary.locator(
        f'[data-remove-blocker="{active_blocker}"]'
    ).get_attribute("aria-label") == "Remove blocker Active blocker"
    assert summary.locator(
        f'[data-remove-blocker="{shared_blocker}"]'
    ).get_attribute("aria-label") == "Remove blocker Shared blocker"

    _post_stage(server, active_blocker, "done")
    page.wait_for_function(
        "id => !document.querySelector(`[data-remove-blocker=\"${id}\"]`)",
        arg=active_blocker,
        timeout=WAIT_MS,
    )
    assert "Shared blocker" in summary.inner_text()

    summary.locator(f'[data-remove-blocker="{shared_blocker}"]').click()
    page.wait_for_selector("[data-blocker-summary]", state="detached", timeout=WAIT_MS)
    detail = _get_ticket(server, blocked_ticket)
    assert detail["stage"] == "needs_kickoff"
    assert detail["blocked"] is False
    assert "blocker_summary" not in detail

    later_blocker = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Later blocker",
    )["id"]
    later_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Later-stage dependent",
        "--blocked-by",
        later_blocker,
    )["id"]
    with sqlite3.connect(server.db_path) as conn:
        conn.execute("UPDATE tickets SET stage = 'needs_plan' WHERE id = ?", (later_ticket,))
    page.goto(f"{server.base}/#/ticket/{later_ticket}")
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{later_ticket}"] '
        f'[data-remove-blocker="{later_blocker}"]',
        timeout=WAIT_MS,
    )
    page.locator(f'[data-remove-blocker="{later_blocker}"]').click()
    page.wait_for_selector("[data-blocker-summary]", state="detached", timeout=WAIT_MS)
    assert _get_ticket(server, later_ticket)["stage"] == "needs_plan"
