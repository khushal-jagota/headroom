"""A Ticket opens inside the Workspace pane, and its eyebrow links its Sprint Item.

Both claims need a real browser against a real server. The first is hash routing: an old
`#/ticket/<id>` address must replace itself with the Workspace address and still render
the Ticket. The second is what the rendered eyebrow contains: no Sprint fact, and a
Sprint Item fact that is a link to that Item on the Workspace. Neither is provable from
source text or from a unit test, because both depend on the router and the live Ticket
resource together.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

SPRINT_NAME = "Workspace Ticket sprint"
START = "2026-01-05"
END = "2026-01-16"
ITEM_TITLE = "Opening Tickets in the Workspace"
ITEM_PROJECT = "Vylo"
TICKET_TITLE = "A Ticket under the Item"


def _an_item_with_a_ticket(
    server: ServerHandle, api: ApiHelper, cli: Callable[..., JsonObject]
) -> tuple[str, str]:
    sprint = api.direct_post(
        server,
        "/api/sprints",
        {"name": SPRINT_NAME, "date_start": START, "date_end": END},
    )
    item_id: str = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        ITEM_TITLE,
        "--project",
        ITEM_PROJECT,
        "--sprint",
        sprint["id"],
        "--priority",
        "P2",
    )["id"]
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        TICKET_TITLE,
        "--sprint-item",
        item_id,
    )["id"]
    return item_id, ticket_id


def test_a_legacy_ticket_address_opens_the_ticket_in_the_workspace(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """`#/ticket/<id>` replaces itself with the Workspace address and renders the Ticket."""
    _, ticket_id = _an_item_with_a_ticket(server, api, cli)

    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )

    page.wait_for_url(f"**/#/workspace/{ticket_id}", timeout=WAIT_MS)
    # The Workspace frame is around it, so this is the pane and not a standalone page.
    assert page.locator(".board-workspace-shell--ticket").count() == 1


def test_the_eyebrow_drops_the_sprint_and_links_the_sprint_item(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """The eyebrow states no Sprint. Its Sprint Item fact opens that Item on the Workspace."""
    item_id, ticket_id = _an_item_with_a_ticket(server, api, cli)

    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )

    assert page.locator("[data-sprint-control]").count() == 0
    assert SPRINT_NAME not in page.inner_text("[data-ticket-identity]")

    fact = page.locator("[data-sprint-item-control]")
    fact.wait_for(timeout=WAIT_MS)
    # The eyebrow uppercases its facts in CSS, so read the text content.
    fact_text = fact.text_content()
    assert fact_text is not None
    assert fact_text.strip() == ITEM_TITLE
    assert fact.get_attribute("href") == f"#/workspace/item/{item_id}"

    fact.click()
    page.wait_for_url(f"**/#/workspace/item/{item_id}", timeout=WAIT_MS)
    page.wait_for_selector("[data-sprint-item-workspace]", timeout=WAIT_MS)
