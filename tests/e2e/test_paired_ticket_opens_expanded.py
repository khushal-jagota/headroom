"""A Ticket conversation follows the current status, on arrival and in a browser.

Paired is work the person is doing with the worker right now, so a paired Ticket opens
the conversation full. Every other status opens at rest. A live status change updates
the same mounted Ticket page without a new visit.

Nothing here needs an agent. A Ticket is parked on a filed proposal, a reply is reported
straight to the reply door the way the ticket screen reports one, and that is what moves it
to paired. Everything else is the real page against the real server.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

TICKET_SCREEN = '[data-screen="ticket"]'
PANE = f"{TICKET_SCREEN} [data-conversation-pane]"
PROPOSAL = "# Success criteria\n\nThe suite goes green.\n"
APPROACH = "# Approach\n\nThe page follows the current Ticket status.\n"


def _a_ticket_on_a_proposal(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper, title: str
) -> str:
    """Create a Ticket that waits for approval of a worker proposal."""
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        title,
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success criteria proposed.",
        ticket_id=ticket_id,
        stdin=PROPOSAL,
    )
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_approval"
    return ticket_id


def _a_paired_ticket(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> str:
    """A Ticket parked on a proposal that its owner has since replied to, which pairs it."""
    ticket_id = _a_ticket_on_a_proposal(server, cli, api, "Paired work")
    # A reply is what pairs it. The server owns that move; the reply door is how it is told.
    paired = api.direct_post(server, f"/api/tickets/{ticket_id}/human-reply", {})
    assert paired["ticket_status"] == "paired", paired
    return ticket_id


def _the_ticket_page(
    server: ServerHandle,
    context: BrowserContext,
    open_page: Callable[..., Page],
    ticket_id: str,
) -> Page:
    page = open_page(
        context,
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    page.wait_for_selector("[data-conversation-layer-host]", timeout=WAIT_MS)
    return page


def test_a_paired_ticket_opens_full_on_arrival(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """A paired Ticket opens at full height."""
    ticket_id = _a_paired_ticket(server, cli, api)
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id)

    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)


def test_an_awaiting_approval_ticket_opens_at_rest_on_arrival(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """An awaiting-approval Ticket opens at the normal non-full state."""
    ticket_id = _a_ticket_on_a_proposal(server, cli, api, "Awaiting approval work")
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id)

    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)


def test_a_live_status_change_resets_the_mounted_ticket_conversation(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """A mounted Ticket follows a live change from paired to awaiting approval."""
    ticket_id = _a_paired_ticket(server, cli, api)
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id)
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)

    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Approach proposed.",
        ticket_id=ticket_id,
        stdin=APPROACH,
    )
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_approval"

    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)
