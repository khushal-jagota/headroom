"""Browser coverage for derived blocker placement, detail, and removal."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

WAIT_MS = 10_000


def _post_stage(server: ServerHandle, ticket_id: str, stage: str) -> JsonObject:
    resp = httpx.post(
        f"{server.base}/api/tickets/{ticket_id}/stage",
        json={"to_stage": stage},
        timeout=10.0,
    )
    assert resp.status_code < 300, f"POST Stage -> {resp.status_code}: {resp.text}"
    body: JsonObject = resp.json()
    return body


def _get_ticket(server: ServerHandle, ticket_id: str) -> JsonObject:
    resp = httpx.get(f"{server.base}/api/tickets/{ticket_id}", timeout=10.0)
    assert resp.status_code < 300, f"GET ticket -> {resp.status_code}: {resp.text}"
    body: JsonObject = resp.json()
    return body


def test_workspace_places_post_kickoff_dependents_in_quiet_blocked_section(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
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
            "UPDATE tickets SET stage = 'needs_plan', ticket_status = 'awaiting_user_review' "
            "WHERE id = ?",
            (later_dependent,),
        )
        conn.execute(
            "UPDATE tickets SET stage = 'needs_approach', ticket_status = 'blocked' WHERE id = ?",
            (shared_dependent,),
        )
    for ticket_id in (blocker, kickoff_dependent, later_dependent, shared_dependent):
        api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        '[data-workspace-view="attention"]',
    )
    page.click('[data-workspace-view="attention"]')
    page.wait_for_selector('[data-workspace-view="all"]', timeout=WAIT_MS)
    no_item = "[data-no-item]"
    active_card = f'[data-card][data-ticket-id="{blocker}"]'
    later_card = f'[data-card][data-ticket-id="{later_dependent}"]'
    kickoff_card = f'[data-card][data-ticket-id="{kickoff_dependent}"]'
    shared_card = f'[data-card][data-ticket-id="{shared_dependent}"]'

    # Everything on today exposes quiet blocked and resting Tickets. Unclassified
    # Tickets share the No Item tail, while their own status and stage remain intact.
    for card in (active_card, later_card, kickoff_card, shared_card):
        assert page.locator(f"{no_item} {card}").is_visible()
    assert page.get_attribute(active_card, "data-ticket-status") == "empty"
    assert page.get_attribute(later_card, "data-ticket-status") == "awaiting_user_review"
    assert page.get_attribute(kickoff_card, "data-ticket-status") == "awaiting_user_review"
    assert page.get_attribute(shared_card, "data-ticket-status") == "blocked"
    assert page.get_attribute(later_card, "data-ticket-stage") == "needs_plan"
    assert page.get_attribute(
        f"{later_card} .board-workspace-stage-mark", "data-agent-working"
    ) == "false"
    assert page.get_attribute(shared_card, "data-ticket-stage") == "needs_approach"

    # Removing the last live blocker settles the same row back to empty.
    cli(server, "ticket", "unblock", shared_dependent, "--by", blocker)
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-ticket-status') "
        "=== 'empty'",
        arg=shared_card,
        timeout=WAIT_MS,
    )

    _post_stage(server, blocker, "done")
    page.wait_for_selector(active_card, state="detached", timeout=WAIT_MS)
    assert page.locator(f"{no_item} {kickoff_card}").count() == 1
    assert page.locator(f"{no_item} {later_card}").count() == 1
    assert _get_ticket(server, later_dependent)["stage"] == "needs_plan"
    assert _get_ticket(server, shared_dependent)["stage"] == "needs_approach"


def test_ticket_detail_shows_only_active_direct_blockers_and_removes_each_link(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
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
    page = open_page(context_factory(), server, f"#/workspace/{blocked_ticket}", ready)

    # Direct blockers belong to the whole Ticket, so they stay in one masthead
    # line even while Kickoff is awaiting approval.
    row = page.locator("[data-blocker-summary]")
    row.wait_for(timeout=WAIT_MS)
    assert row.locator("[data-blocker-chip]").count() == 2
    assert "Cleared blocker" not in row.inner_text()
    active_chip = row.locator(f'[data-blocker-chip="{active_blocker}"]')
    assert "Active blocker" in active_chip.inner_text()
    assert active_chip.locator(f'a[href="#/workspace/{active_blocker}"]').count() == 1
    assert row.locator(
        f'[data-remove-blocker="{active_blocker}"]'
    ).get_attribute("aria-label") == "Remove blocker Active blocker"
    assert row.locator(
        f'[data-remove-blocker="{shared_blocker}"]'
    ).get_attribute("aria-label") == "Remove blocker Shared blocker"

    _post_stage(server, active_blocker, "done")
    page.wait_for_function(
        "id => !document.querySelector(`[data-remove-blocker=\"${id}\"]`)",
        arg=active_blocker,
        timeout=WAIT_MS,
    )
    assert "Shared blocker" in row.inner_text()

    row.locator(f'[data-remove-blocker="{shared_blocker}"]').click()
    page.wait_for_selector("[data-blocker-chip]", state="detached", timeout=WAIT_MS)
    assert page.locator("[data-blocker-summary]").count() == 0
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
    page.goto(f"{server.base}/#/workspace/{later_ticket}")
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{later_ticket}"] '
        f'[data-remove-blocker="{later_blocker}"]',
        timeout=WAIT_MS,
    )
    page.locator(f'[data-remove-blocker="{later_blocker}"]').click()
    page.wait_for_selector("[data-blocker-summary]", state="detached", timeout=WAIT_MS)
    assert _get_ticket(server, later_ticket)["stage"] == "needs_plan"


def test_kickoff_card_context_approves_while_blockers_stay_in_the_masthead(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    blocker = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Standing blocker"
    )["id"]
    ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Kickoff card ticket",
        "--kickoff-note",
        "Ready to start",
        "--blocked-by",
        blocker,
    )["id"]
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket}"]'
    page = open_page(context_factory(), server, f"#/workspace/{ticket}", ready)

    # Worker setup stays in the approval card; blockers stay on the Ticket itself.
    card = '[data-approval-block][data-mode="gating-pending"][data-field="kickoff"]'
    page.wait_for_selector(f"{card} [data-approval-context-row]", timeout=WAIT_MS)
    assert page.locator(
        f"{card} .approval-proposal-shell > [data-approval-context-row] + .approval-actions"
    ).count() == 1
    row = f"{card} [data-approval-context-row]"
    assert page.locator(f"{row} [data-employee-configuration-setup]").count() == 1
    assert page.locator(f"{row} [data-blocker-chip]").count() == 0
    assert page.locator(f'[data-blocker-summary] [data-blocker-chip="{blocker}"]').count() == 1

    # Kickoff approval with an explicit ceiling/at-cap still works from the card.
    page.select_option(f"{card} [data-scope-ceiling]", "none")
    page.select_option(f"{card} [data-scope-atcap] select", "user_review")
    page.click(f"{card} [data-accept]")

    # Kickoff approval does not move the ticket-level blocker line.
    page.wait_for_selector("[data-approval-context-row]", state="detached", timeout=WAIT_MS)
    assert page.locator(f'[data-blocker-summary] a[href="#/workspace/{blocker}"]').count() == 1
    detail = _get_ticket(server, ticket)
    assert detail["stage"] == "needs_success"
    assert detail["ceiling"] == "needs_success"
    assert detail["at_cap"] == "user_review"

    # A later-stage approval card carries no context row; blockers stay standalone.
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success ready for review.",
        ticket_id=ticket,
        stdin="Success proposal body",
    )
    later_card = '[data-approval-block][data-mode="gating-pending"][data-field="success"]'
    page.wait_for_selector(later_card, state="attached", timeout=WAIT_MS)
    assert page.locator(f"{later_card} [data-approval-context-row]").count() == 0
    assert page.locator("[data-approval-context-row]").count() == 0
    assert page.locator("[data-blocker-summary]").count() == 1
