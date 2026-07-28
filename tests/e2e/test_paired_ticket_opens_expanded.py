"""A paired Ticket opens straight into the full conversation, on arrival, in a browser.

Paired is work the person is doing with the worker right now, so landing on a paired
Ticket puts the conversation full rather than making them open it. The page seeds this
once, the first time the status is known on a visit — it is the state the page opens in,
not a rule it keeps enforcing. So a person who puts a paired conversation away keeps it
away: nothing the page hears afterwards on that visit opens it again, and only the next
arrival seeds it full once more.

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
COLLAPSE = f"{TICKET_SCREEN} [data-conversation-collapse]"
TITLE = f"{TICKET_SCREEN} .ticket-title"
PROPOSAL = "# Success criteria\n\nThe suite goes green.\n"


def _a_paired_ticket(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> str:
    """A Ticket parked on a proposal that its owner has since replied to, which pairs it."""
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Paired work",
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
    """Landing on a paired Ticket, the conversation is already at full height.

    The plain coding Ticket its three-state neighbour opens lands at rest; this one is
    paired, and the only difference between them is the status the page seeds from.
    """
    ticket_id = _a_paired_ticket(server, cli, api)
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id)

    # The seed, the moment the status is known: full height, not rest.
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)


def test_a_paired_conversation_put_away_stays_away_for_the_visit(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    """Seeded once, then the person's own controls own it.

    The page opens a paired conversation full. The person collapses it. Then something
    lands under the page — a real write the browser hears and refetches on — and the pane
    must stay where the person left it: the seed is an opening state, not a clamp that
    re-opens on every change while the Ticket is paired.
    """
    ticket_id = _a_paired_ticket(server, cli, api)
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id)
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)

    # The person puts it away. Opened's control steps it back to peeked.
    page.click(COLLAPSE, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS)

    # A real write lands under the page while it is still paired. The browser hears the
    # change signal and refetches the Ticket, which re-runs the page's opening logic.
    renamed = "Paired work, renamed under the page"
    api.direct_patch(server, f"/api/tickets/{ticket_id}", {"title": renamed})
    # Wait for that refetch to actually reach the screen, so the re-run has happened.
    page.wait_for_function(
        "([selector, text]) => document.querySelector(selector)?.textContent.includes(text)",
        arg=[TITLE, renamed],
        timeout=WAIT_MS,
    )
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "paired"

    # Still where the person left it. A clamp would have snapped it back to opened.
    state = page.evaluate(
        "() => document.querySelector('[data-conversation-pane]')?.dataset.conversationState"
    )
    assert state == "peeked", ("the seed re-opened a conversation the person put away", state)
