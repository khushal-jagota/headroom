"""A person answering a Ticket's worker, in the browser where they answer it.

A Ticket parked on a filed proposal is waiting for its owner. Replying to the worker is
an answer of a kind — the proposal is being discussed rather than approved — so the
Ticket moves to paired. The ticket screen is the one place that knows both halves, and it
says a reply happened only once the conversation has taken the message.

No agent is involved and none is needed. The send is held inside the page and answered
with whatever fate this test chooses, so nothing is ever spawned. Everything else is real
and reaches the real server, including the call that moves the Ticket.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, Request
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_dev_conversation_pane import HOLD_THE_SEND

TICKET_SCREEN = '[data-screen="ticket"]'
COMPOSER = f"{TICKET_SCREEN} [data-conversation-input]"
SEND = f"{TICKET_SCREEN} [data-conversation-send]"
FATE = f"{TICKET_SCREEN} [data-conversation-fate]"
PROPOSAL = "# Success criteria\n\nThe suite goes green.\n"
REFUSED_TEXT = "did this reach anything"


def _parked_on_a_proposal(server: ServerHandle, cli: Callable[..., JsonObject]) -> str:
    """A Ticket its worker has filed a proposal on, waiting for its owner to answer."""
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Answer the worker",
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
    return ticket_id


def test_a_reply_in_the_pane_pairs_the_ticket_and_a_refusal_leaves_it_parked(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = _parked_on_a_proposal(server, cli)
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_approval"
    # Nothing is seeded: a Ticket nobody has spoken to has no conversation, and the first
    # message is what makes one.

    context = context_factory()
    context.add_init_script(HOLD_THE_SEND)
    page = open_page(
        context,
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )

    replies: list[str] = []

    def note_reply(request: Request) -> None:
        if request.method == "POST" and request.url.endswith("/human-reply"):
            replies.append(request.url)

    page.on("request", note_reply)
    page.wait_for_selector(f"{COMPOSER}:not([disabled])", timeout=WAIT_MS)

    # First, a message the conversation turns away. The words coming back to the person
    # who wrote them is the pane saying this send is over and got nowhere — so anything
    # it was going to do afterwards it has already not done.
    page.fill(COMPOSER, REFUSED_TEXT, timeout=WAIT_MS)
    page.click(SEND, timeout=WAIT_MS)
    page.wait_for_function("() => window.__heldSends.length === 1", timeout=WAIT_MS)
    # The owner's door answers with the fate and the conversation it happened in. This
    # message was to make one and did not land, so there is no conversation to name.
    page.evaluate(
        "() => window.__heldSends[0].answer("
        "{ conversation_id: null, fate: 'refused', refusal_reason: 'backend_did_not_start' })"
    )
    page.wait_for_function(
        "([selector, text]) => document.querySelector(selector).value === text",
        arg=[COMPOSER, REFUSED_TEXT],
        timeout=WAIT_MS,
    )
    page.wait_for_selector(FATE, timeout=WAIT_MS)
    assert "not delivered" in page.inner_text(FATE)
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_approval"

    # Now one the conversation holds for a busy agent. Held is reached, so this one is a
    # reply, and the screen says so as soon as the send comes back.
    page.fill(COMPOSER, "here is what I think of that", timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith(f"/api/tickets/{ticket_id}/human-reply")
        and response.status < 300,
        timeout=WAIT_MS,
    ):
        page.click(SEND, timeout=WAIT_MS)
        page.wait_for_function("() => window.__heldSends.length === 2", timeout=WAIT_MS)
        page.evaluate(
            "() => window.__heldSends[1].answer("
            "{ conversation_id: 'conv_made_by_the_message', fate: 'queued', queue_position: 1 })"
        )

    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "paired"
    # One reply, from the send that got somewhere. The refused send is in front of it in
    # this page's own order, so a reply it had made would be counted here too.
    assert replies == [f"{server.base}/api/tickets/{ticket_id}/human-reply"]
