from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

TITLE = "Mistaken ticket delete browser proof"


def _assert_no_delete_control(page: Page) -> None:
    assert page.get_by_role("button", name="Delete ticket", exact=True).count() == 0
    assert page.locator("[data-ticket-delete]").count() == 0


def test_ticket_ui_has_no_delete_control(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create", "--worker-type", "coding",
        "--title",
        TITLE,
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    ctx = context_factory()
    page = open_page(
        ctx,
        server,
        f"#/ticket/{ticket_id}",
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _assert_no_delete_control(page)

    page.goto(f"{server.base}/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=10_000)
    page.click(f'[data-card][data-ticket-id="{ticket_id}"]')
    page.wait_for_selector(
        'section[data-screen="workspace"] '
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        timeout=10_000,
    )
    _assert_no_delete_control(page)
