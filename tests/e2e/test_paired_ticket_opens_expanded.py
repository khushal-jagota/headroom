"""Ticket status selects conversation presentation only when a visit starts.

Paired is work the person is doing with the worker right now, so a paired Ticket opens
the conversation full. Every other status opens at rest. After arrival, the person owns
the state until navigation starts a visit to another Ticket.

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
INPUT = f"{TICKET_SCREEN} [data-conversation-input]"
COLLAPSE = f"{TICKET_SCREEN} [data-conversation-collapse]"
EXPAND = f"{TICKET_SCREEN} [data-conversation-expand]"
TITLE = f"{TICKET_SCREEN} .ticket-title"
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
        "--recap",
        "Success criteria proposed.",
        ticket_id=ticket_id,
        stdin=PROPOSAL,
    )
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_user_review"
    return ticket_id


def _a_paired_ticket(server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper) -> str:
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


def _wait_for_ticket_refresh(
    server: ServerHandle, api: ApiHelper, page: Page, ticket_id: str, title: str
) -> None:
    """Land a visible write after a status change, then wait until the page receives it."""
    api.direct_patch(server, f"/api/tickets/{ticket_id}", {"title": title})
    page.wait_for_function(
        "([selector, text]) => document.querySelector(selector)?.textContent.includes(text)",
        arg=[TITLE, title],
        timeout=WAIT_MS,
    )


def test_a_paired_visit_stays_open_when_status_changes_to_awaiting_approval(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """A live change away from paired does not close the mounted conversation."""
    ticket_id = _a_paired_ticket(server, cli, api)
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id)
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)

    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Approach proposed.",
        ticket_id=ticket_id,
        stdin=APPROACH,
    )
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_user_review"

    _wait_for_ticket_refresh(server, api, page, ticket_id, "Still open after proposal")
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)


def test_a_non_paired_visit_stays_at_rest_when_status_changes_to_paired(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """A live change to paired does not open the mounted conversation."""
    ticket_id = _a_ticket_on_a_proposal(server, cli, api, "Pair after arrival")
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id)
    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)

    paired = api.direct_post(server, f"/api/tickets/{ticket_id}/human-reply", {})
    assert paired["ticket_status"] == "paired", paired

    _wait_for_ticket_refresh(server, api, page, ticket_id, "Still at rest after pairing")
    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)
