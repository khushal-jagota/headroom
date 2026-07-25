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
            "UPDATE tickets SET stage = 'needs_approach', ticket_status = 'blocked' WHERE id = ?",
            (shared_dependent,),
        )
    for ticket_id in (blocker, kickoff_dependent, later_dependent, shared_dependent):
        api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        f'[data-card][data-ticket-id="{later_dependent}"]',
    )
    blocked = '[data-bucket-section][data-bucket-key="blocked"]'
    empty = '[data-bucket-section][data-bucket-key="empty"]'
    approval = '[data-bucket-section][data-bucket-key="awaiting_approval"]'
    active_card = f'[data-card][data-ticket-id="{blocker}"]'
    later_card = f'[data-card][data-ticket-id="{later_dependent}"]'
    kickoff_card = f'[data-card][data-ticket-id="{kickoff_dependent}"]'
    shared_card = f'[data-card][data-ticket-id="{shared_dependent}"]'

    # Blocked is the resting status of a ticket held by a live blocker, and its
    # group collapses by default. A ticket with a live blocker that carries any
    # other status — a parked kickoff proposal, a later approval — groups by
    # that status instead.
    assert page.locator(blocked).get_attribute("open") is None
    assert page.locator(empty).get_attribute("open") is not None
    assert page.locator(f"{empty} {active_card}").is_visible()
    assert page.locator(approval).get_attribute("open") is not None
    assert page.locator(f"{approval} {later_card}").is_visible()
    assert page.locator(f"{blocked} {later_card}").count() == 0
    assert page.locator(f"{approval} {kickoff_card}").count() == 1
    assert page.locator(f"{blocked} {kickoff_card}").count() == 0
    assert not page.locator(f"{blocked} {shared_card}").is_visible()
    page.locator(f"{blocked} > summary").click()
    assert page.locator(blocked).get_attribute("open") is not None
    page.wait_for_selector(f"{blocked} {shared_card}", state="visible", timeout=WAIT_MS)
    assert page.locator(f"{blocked} {shared_card}").count() == 1
    assert page.get_attribute(later_card, "data-ticket-stage") == "needs_plan"
    assert page.get_attribute(
        f"{later_card} .board-workspace-stage-mark", "data-agent-working"
    ) == "false"
    assert "Prerequisite" not in page.inner_text(f"{blocked} > .disclosure-body")

    # Removing the last live blocker settles the ticket back to empty, so the
    # card moves out of Blocked and the emptied group stops rendering.
    cli(server, "ticket", "unblock", shared_dependent, "--by", blocker)
    page.wait_for_selector(f"{empty} {shared_card}", timeout=WAIT_MS)
    assert page.locator(blocked).count() == 0

    _post_stage(server, blocker, "done")
    page.wait_for_selector(
        f'[data-bucket-section][data-bucket-key="done"] {active_card}',
        state="attached",
        timeout=WAIT_MS,
    )
    assert page.locator(f"{approval} {kickoff_card}").count() == 1
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
    page = open_page(context_factory(), server, f"#/ticket/{blocked_ticket}", ready)

    # At needs_kickoff the direct blockers render as chips inside the Kickoff
    # approval card's context row; the standalone section is suppressed.
    row = page.locator(
        '[data-approval-block][data-field="kickoff"] [data-approval-context-row]'
    )
    row.wait_for(timeout=WAIT_MS)
    assert page.locator("[data-blocker-summary]").count() == 0
    assert row.locator("[data-blocker-chip]").count() == 2
    assert "Cleared blocker" not in row.inner_text()
    active_chip = row.locator(f'[data-blocker-chip="{active_blocker}"]')
    assert "Active blocker" in active_chip.inner_text()
    assert active_chip.locator(f'a[href="#/ticket/{active_blocker}"]').count() == 1
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
    page.goto(f"{server.base}/#/ticket/{later_ticket}")
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{later_ticket}"] '
        f'[data-remove-blocker="{later_blocker}"]',
        timeout=WAIT_MS,
    )
    page.locator(f'[data-remove-blocker="{later_blocker}"]').click()
    page.wait_for_selector("[data-blocker-summary]", state="detached", timeout=WAIT_MS)
    assert _get_ticket(server, later_ticket)["stage"] == "needs_plan"


def test_kickoff_card_context_row_approves_and_standalone_blockers_return(
    server, context_factory, open_page, cli
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
    page = open_page(context_factory(), server, f"#/ticket/{ticket}", ready)

    # Structure: worker pills and blocker chips inside the approval card, in one
    # context row directly above the Approve/ceiling action row.
    card = '[data-approval-block][data-mode="gating-pending"][data-field="kickoff"]'
    page.wait_for_selector(f"{card} [data-approval-context-row]", timeout=WAIT_MS)
    assert page.locator(
        f"{card} .approval-proposal-shell > [data-approval-context-row] + .approval-actions"
    ).count() == 1
    row = f"{card} [data-approval-context-row]"
    assert page.locator(f"{row} [data-employee-configuration-setup]").count() == 1
    assert page.locator(f'{row} [data-blocker-chip="{blocker}"]').count() == 1
    assert page.locator("[data-blocker-summary]").count() == 0

    # Kickoff approval with an explicit ceiling/at-cap still works from the card.
    page.select_option(f"{card} [data-scope-ceiling]", "none")
    page.select_option(f"{card} [data-scope-atcap] select", "propose")
    page.click(f"{card} [data-accept]")

    # After Kickoff is approved the standalone blocker section is back and the
    # context row is gone.
    page.wait_for_selector("[data-blocker-summary]", timeout=WAIT_MS)
    page.wait_for_selector("[data-approval-context-row]", state="detached", timeout=WAIT_MS)
    assert page.locator(f'[data-blocker-summary] a[href="#/ticket/{blocker}"]').count() == 1
    detail = _get_ticket(server, ticket)
    assert detail["stage"] == "needs_success"
    assert detail["ceiling"] == "needs_success"
    assert detail["at_cap"] == "propose"

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
