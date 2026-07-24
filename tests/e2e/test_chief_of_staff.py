"""Focused chief-of-staff route smoke tests.

These are intentionally unanchored so they do not affect the verify item scorer.
"""

from __future__ import annotations

WAIT_MS = 10_000


def _workspace_ticket(ticket_id: str) -> str:
    return (
        'section[data-screen="workspace"] '
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    )


def test_chief_of_staff_route_nav_and_acp_mount(server, context_factory, open_page) -> None:
    page = open_page(
        context_factory(),
        server,
        "#/chief",
        'section[data-screen="chief"] [data-chat-input]',
        settled=False,
    )

    assert page.query_selector('a.nav-link[data-screen="chief"]') is None
    assert page.inner_text("h1") == "Chief of Staff"
    assert page.get_attribute("[data-chat-input]", "placeholder") == "Message Chief of Staff..."

    assert page.locator("[data-acp-conversation-pane]").count() == 1


def test_workspace_defaults_to_chief_chat_and_ticket_selection_restores(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Workspace selectable ticket",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": tid})

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        'section[data-screen="workspace"] [data-chat-input]',
        settled=True,
    )

    assert "active" in (
        page.get_attribute('a.nav-link[data-screen="workspace"]', "class") or ""
    )
    assert page.url == f"{server.base}/#/workspace"
    assert page.get_attribute("[data-chat-input]", "placeholder") == "Message Chief of Staff..."
    assert page.locator("[data-hide-done-toggle]").count() == 0
    assert page.locator('[aria-label="Ticket status"]').count() == 0

    card = f'[data-card][data-ticket-id="{tid}"]'
    page.click(card)
    page.wait_for_url(f"{server.base}/#/workspace/{tid}", timeout=WAIT_MS)
    ticket = _workspace_ticket(tid)
    page.wait_for_selector(f"{ticket} [data-chat-input]", timeout=WAIT_MS)
    assert page.query_selector('[aria-label="Workspace tickets by status"]') is not None
    assert page.inner_text(f"{ticket} .ticket-title") == "Workspace selectable ticket"

    page.click("[data-chief-of-staff-button]")
    page.wait_for_url(f"{server.base}/#/workspace", timeout=WAIT_MS)
    page.wait_for_selector(ticket, state="detached", timeout=WAIT_MS)
    page.wait_for_selector('section[data-screen="workspace"] [data-chat-input]', timeout=WAIT_MS)
    assert page.locator("[data-acp-conversation-pane]").count() == 1


def test_mobile_workspace_selections_open_standalone_pages(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Mobile workspace ticket",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": tid})

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        f'[data-card][data-ticket-id="{tid}"]',
        settled=True,
    )
    page.set_viewport_size({"width": 390, "height": 844})

    page.click(f'[data-card][data-ticket-id="{tid}"]')
    page.wait_for_url(f"{server.base}/#/ticket/{tid}", timeout=WAIT_MS)
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{tid}"] .ticket-title',
        timeout=WAIT_MS,
    )
    assert page.locator('section[data-screen="workspace"]').count() == 0
    assert page.inner_text(".ticket-title") == "Mobile workspace ticket"

    page.goto(f"{server.base}/#/workspace/{tid}")
    page.wait_for_selector("[data-chief-of-staff-button]", timeout=WAIT_MS)
    page.click("[data-chief-of-staff-button]")
    page.wait_for_url(f"{server.base}/#/chief", timeout=WAIT_MS)
    page.wait_for_selector(
        'section[data-screen="chief"] [data-chat-input]',
        timeout=WAIT_MS,
    )
    assert page.locator('section[data-screen="workspace"]').count() == 0
    assert page.inner_text("h1") == "Chief of Staff"


def test_workspace_ticket_route_restores_on_load_refresh_and_history(
    server, context_factory, open_page, cli, api
) -> None:
    first_id = cli(
        server,
        "ticket",
        "create", "--worker-type", "coding",
        "--title",
        "First routed workspace ticket",
        "--project-id",
        "project_vylo",
    )["id"]
    second_id = cli(
        server,
        "ticket",
        "create", "--worker-type", "coding",
        "--title",
        "Second routed workspace ticket",
        "--project-id",
        "project_vylo",
    )["id"]
    other_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Other workspace ticket",
        "--kickoff-note",
        "Pending kickoff",
    )["id"]
    for ticket_id in (first_id, second_id, other_id):
        api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})
    first_ticket = _workspace_ticket(first_id)
    second_ticket = _workspace_ticket(second_id)
    encoded_first_id = first_id.replace("_", "%5F", 1)

    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{encoded_first_id}",
        first_ticket,
        settled=True,
    )
    assert page.inner_text(".ticket-title") == "First routed workspace ticket"

    page.reload()
    page.wait_for_selector(first_ticket, timeout=WAIT_MS)
    kickoff_bucket = '[data-bucket-section][data-bucket-key="kickoff"]'
    kickoff_summary = f"{kickoff_bucket} > .disclosure-summary"
    page.click(kickoff_summary)
    assert page.get_attribute(kickoff_bucket, "open") is None

    page.click(f'[data-card][data-ticket-id="{second_id}"]')
    page.wait_for_url(f"{server.base}/#/workspace/{second_id}", timeout=WAIT_MS)
    page.wait_for_selector(second_ticket, timeout=WAIT_MS)
    assert page.get_attribute(kickoff_bucket, "open") is None

    page.go_back()
    page.wait_for_url(f"{server.base}/#/workspace/{encoded_first_id}", timeout=WAIT_MS)
    page.wait_for_selector(first_ticket, timeout=WAIT_MS)
    assert page.get_attribute(kickoff_bucket, "open") is None

    page.go_forward()
    page.wait_for_url(f"{server.base}/#/workspace/{second_id}", timeout=WAIT_MS)
    page.wait_for_selector(second_ticket, timeout=WAIT_MS)

    page.go_back()
    page.wait_for_selector(first_ticket, timeout=WAIT_MS)
    cli(server, "ticket", "delete", first_id, "--yes")
    page.wait_for_url(f"{server.base}/#/workspace", timeout=WAIT_MS)
    page.wait_for_selector(
        'section[data-screen="workspace"] [data-chief-of-staff-button][aria-pressed="true"]',
        timeout=WAIT_MS,
    )

    page.goto(f"{server.base}/#/workspace/t_missing")
    page.wait_for_url(f"{server.base}/#/workspace", timeout=WAIT_MS)
    page.wait_for_selector(
        'section[data-screen="workspace"] [data-chief-of-staff-button][aria-pressed="true"]',
        timeout=WAIT_MS,
    )
    assert (
        page.locator('section[data-screen="workspace"] section[data-screen="ticket"]').count()
        == 0
    )


def test_legacy_board_route_renders_workspace(server, context_factory, open_page) -> None:
    page = open_page(
        context_factory(),
        server,
        "#/board",
        'section[data-screen="workspace"] [data-chief-of-staff-button]',
        settled=False,
    )

    assert "active" in (
        page.get_attribute('a.nav-link[data-screen="workspace"]', "class") or ""
    )
