"""Focused chief-of-staff route smoke tests.

These are intentionally unanchored so they do not affect the verify item scorer.
"""

from __future__ import annotations

from playwright.sync_api import Page

WAIT_MS = 10_000


def _wait_chat_text(page: Page, who: str, text: str) -> None:
    page.wait_for_function(
        "({ who, text }) => Array.from(document.querySelectorAll(`[data-chat-msg=\"${who}\"]`))"
        ".some(el => el.textContent.includes(text))",
        arg={"who": who, "text": text},
        timeout=WAIT_MS,
    )


def test_chief_of_staff_route_nav_and_chat(server, context_factory, open_page) -> None:
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

    page.fill("[data-chat-input]", "triage the workspace")
    page.click("[data-chat-send]")

    _wait_chat_text(page, "you", "triage the workspace")
    _wait_chat_text(page, "planner", "echo: triage the workspace")


def test_workspace_defaults_to_chief_chat_and_ticket_selection_restores(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(server, "ticket", "create", "--title", "Workspace selectable ticket")["id"]
    api.human_post(server, "/api/day/today/tickets", {"ticket_id": tid})

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
    assert page.get_attribute("[data-chat-input]", "placeholder") == "Message Chief of Staff..."
    page.wait_for_selector('[data-workspace-filters] [data-status-filter="all"]', timeout=WAIT_MS)
    assert page.inner_text('[data-workspace-filters] [data-filter-group="ticket-status"]')

    page.fill("[data-chat-input]", "triage from workspace")
    page.click("[data-chat-send]")
    _wait_chat_text(page, "you", "triage from workspace")
    _wait_chat_text(page, "planner", "echo: triage from workspace")

    card = f'[data-card][data-ticket-id="{tid}"]'
    page.click(card)
    page.wait_for_selector(f'.board-workspace-open-ticket[href="#/ticket/{tid}"]', timeout=WAIT_MS)
    assert page.inner_text(".board-workspace-open-ticket") == "Workspace selectable ticket"

    page.click("[data-chief-of-staff-button]")
    page.wait_for_selector('section[data-screen="workspace"] [data-chat-input]', timeout=WAIT_MS)
    _wait_chat_text(page, "you", "triage from workspace")
    _wait_chat_text(page, "planner", "echo: triage from workspace")


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
