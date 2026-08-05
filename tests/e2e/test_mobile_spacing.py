"""Browser contracts for the shared mobile gutter and surface inset."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle

from planner.conversation.events import AgentMessageEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore


def _content_width(page: Page, selector: str) -> int:
    return int(
        page.locator(selector).first.evaluate(
            """element => {
              const style = getComputedStyle(element);
              const box = element.getBoundingClientRect();
              return Math.round(
                box.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
              );
            }"""
        )
    )


def _seed_conversation(server: ServerHandle, ticket_id: str) -> None:
    conversation_id = "conv-mobile-spacing"
    response = httpx.post(
        f"{server.base}/api/conversation/conversations",
        json={
            "conversation_id": conversation_id,
            "backend_key": "codex",
            "model": "e2e-model",
        },
        timeout=10.0,
    )
    assert response.status_code == 201, response.text
    with sqlite3.connect(server.db_path) as connection:
        connection.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (conversation_id, ticket_id),
        )

    async def append_message() -> None:
        store = ConversationStore(str(server.db_path))
        await store.append_event(
            conversation_id,
            AgentMessageEventPayload(
                content=text_message_content("A worker message that spans the mobile pane.")
            ),
        )

    errors: list[BaseException] = []

    def run_append() -> None:
        try:
            asyncio.run(append_message())
        except BaseException as error:  # noqa: BLE001 - re-raised on the test thread
            errors.append(error)

    writer = threading.Thread(target=run_append)
    writer.start()
    writer.join()
    if errors:
        raise errors[0]


def _seed_spacing_ticket(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> str:
    ticket_id = str(
        cli(
            server,
            "ticket",
            "create",
            "--worker-type",
            "coding",
            "--title",
            "A ticket with one mobile spacing surface",
        )["id"]
    )
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})
    api.direct_put(
        server,
        f"/api/tickets/{ticket_id}/recap",
        {"body": "The ticket recap keeps one surface inset."},
    )
    api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/scope",
        {"ceiling": "needs_implementation", "at_cap": "propose"},
    )
    for field, body in (
        ("success", "The mobile spacing system is measurable."),
        ("approach", "The frame owns the gutter and surfaces own one inset."),
        ("plan", "Edit the tokens, app rules, and browser contracts."),
    ):
        cli(
            server,
            "worker",
            "propose",
            "--body-file",
            "-",
            "--recap",
            f"{field} ready.",
            ticket_id=ticket_id,
            stdin=body,
        )
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Implementation ready.",
        ticket_id=ticket_id,
        stdin="The implementation proposal provides the mobile spacing system.",
    )
    _seed_conversation(server, ticket_id)
    return ticket_id


def test_mobile_spacing_contracts_and_desktop_frame(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = _seed_spacing_ticket(server, cli, api)
    review_card = f'[data-review-card][data-ticket-id="{ticket_id}"]'

    review = open_page(context_factory(), server, "#/review", review_card)
    review.set_viewport_size({"width": 390, "height": 844})
    assert _content_width(review, f"{review_card} .approval-draft") == 326
    assert review.evaluate(
        """() => ({
          pageGutter: getComputedStyle(document.documentElement)
            .getPropertyValue('--page-gutter').trim(),
          surfacePad: getComputedStyle(document.documentElement)
            .getPropertyValue('--surface-pad').trim(),
        })"""
    ) == {"pageGutter": "16px", "surfacePad": "16px"}

    review.set_viewport_size({"width": 360, "height": 844})
    assert review.evaluate(
        """() => ({
          pageGutter: getComputedStyle(document.documentElement)
            .getPropertyValue('--page-gutter').trim(),
          surfacePad: getComputedStyle(document.documentElement)
            .getPropertyValue('--surface-pad').trim(),
        })"""
    ) == {"pageGutter": "12px", "surfacePad": "16px"}

    review.set_viewport_size({"width": 1280, "height": 900})
    desktop_frame = review.evaluate(
        """() => {
          const shell = getComputedStyle(document.querySelector('.shell-content'));
          const chamber = document.querySelector('.review-screen');
          const chamberStyle = getComputedStyle(chamber);
          const tokens = getComputedStyle(document.documentElement);
          return {
            pageGutter: tokens.getPropertyValue('--page-gutter').trim(),
            surfacePad: tokens.getPropertyValue('--surface-pad').trim(),
            shellPadding: shell.paddingInline,
            chamberWidth: Math.round(chamber.getBoundingClientRect().width),
            chamberMaxWidth: chamberStyle.maxWidth,
            chamberPadding: chamberStyle.paddingInline,
          };
        }"""
    )
    assert desktop_frame == {
        "pageGutter": "24px",
        "surfacePad": "24px",
        "shellPadding": "24px",
        "chamberWidth": 624,
        "chamberMaxWidth": "624px",
        "chamberPadding": "0px",
    }

    ticket_selector = f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    ticket = open_page(context_factory(), server, f"#/ticket/{ticket_id}", ticket_selector)
    ticket.set_viewport_size({"width": 390, "height": 844})
    ticket.wait_for_selector(
        f'{ticket_selector} [data-field="implementation"] [data-mode="gating-pending"]',
        timeout=WAIT_MS,
    )
    implementation_draft = f'{ticket_selector} [data-field="implementation"] .approval-draft'
    implementation_body = f'{ticket_selector} [data-field="implementation"] > .disclosure-body'
    assert _content_width(ticket, implementation_draft) == 316
    assert _content_width(ticket, implementation_body) == 348
    assert _content_width(ticket, f'{ticket_selector} .ticket-recap-inner') == 316
    ticket.click(f'{ticket_selector} [data-conversation-input]')
    ticket.wait_for_selector(
        f'{ticket_selector} [data-conversation-pane][data-conversation-state="peeked"]',
        timeout=WAIT_MS,
    )
    assert _content_width(ticket, f'{ticket_selector} .chat-a') == 314
    assert ticket.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0

    workspace = open_page(
        context_factory(),
        server,
        "#/workspace",
        f'[data-card][data-ticket-id="{ticket_id}"]',
    )
    workspace.set_viewport_size({"width": 390, "height": 844})
    assert _content_width(
        workspace, f'[data-card][data-ticket-id="{ticket_id}"] .list-row-title'
    ) == 261
    assert workspace.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
