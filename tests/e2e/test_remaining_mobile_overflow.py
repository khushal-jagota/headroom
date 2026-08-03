"""Browser coverage for narrow Ticket and conversation containment."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import TypedDict, cast

from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle


class WidthOffender(TypedDict):
    right: int
    localScroll: bool


class WidthTicket(TypedDict):
    scrollWidth: int
    clientWidth: int


class WidthReport(TypedDict):
    viewport: int
    document: int
    body: int
    ticket: WidthTicket | None
    offenders: list[WidthOffender]


def _width_report(page: Page) -> WidthReport:
    return cast(WidthReport, page.evaluate(
        """() => {
          const width = window.innerWidth;
          const visible = element => {
            const box = element.getBoundingClientRect();
            return box.width > 0 && box.height > 0;
          };
          const offenders = [...document.querySelectorAll('*')]
            .filter(element => visible(element))
            .map(element => {
              const box = element.getBoundingClientRect();
              return {
                tag: element.tagName.toLowerCase(),
                className: typeof element.className === 'string' ? element.className : '',
                right: Math.round(box.right),
                left: Math.round(box.left),
                scrollWidth: Math.round(element.scrollWidth),
                clientWidth: Math.round(element.clientWidth),
                localScroll: element.closest(
                  '.markdown table, .markdown pre, .file-preview-document-body, .chat-thread'
                ) !== null,
              };
            })
            .filter(item => item.right > width + 1 || item.scrollWidth > item.clientWidth + 1)
            .sort((a, b) => Math.max(b.right - width, b.scrollWidth - b.clientWidth)
              - Math.max(a.right - width, a.scrollWidth - a.clientWidth))
            .slice(0, 12);
          return {
            viewport: width,
            document: document.documentElement.scrollWidth,
            body: document.body.scrollWidth,
            ticket: (() => {
              const element = document.querySelector('.ticket-doc');
              return element === null ? null : {
                scrollWidth: element.scrollWidth,
                clientWidth: element.clientWidth,
              };
            })(),
            offenders,
          };
        }"""
    ))


def test_remaining_ticket_mobile_overflow_states(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "TicketTitleWithAnIntentionallyLongUnbrokenTokenThatMustWrapInsideTheMobileViewport",
    )["id"]
    api.direct_put(
        server,
        f"/api/tickets/{ticket_id}/recap",
        {
            "body": (
                "| Surface | A deliberately wide value | Another wide value |\n"
                "| --- | --- | --- |\n"
                "| Ticket | keep this table inside its own scroll area | keep the page bounded |"
            )
        },
    )
    wide_value = "value-" + ("x" * 180)
    proposal = (
        f"| Surface | A deliberately wide value | Another wide value |\n"
        f"| --- | --- | --- |\n"
        f"| Ticket | {wide_value} | keep the page bounded |\n\n"
        "`an-intentionally-long-inline-code-token-that-must-wrap-without-widening`"
    )
    fields = {
        "kickoff": {"value": "Kickoff", "proposal": None, "user_note": None},
        "success": {
            "value": None,
            "proposal": {"body": proposal, "proposed_by": "agent", "created_at": 2},
            "user_note": None,
        },
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    with sqlite3.connect(server.db_path) as connection:
        connection.execute(
            "UPDATE tickets SET stage = ?, fields = ?, updated_at = 2 WHERE id = ?",
            ("needs_success", json.dumps(fields), ticket_id),
        )
    started = api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/conversation/send",
        {
            "conversation_id": None,
            "content": [{"piece": "text", "text": "A conversation line for the mobile state."}],
            "sender_label": "owner",
        },
    )
    assert started["conversation_id"] is not None

    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    context = context_factory()
    page = open_page(context, server, f"#/ticket/{ticket_id}", ready)
    page.wait_for_selector(
        '[data-conversation-pane][data-conversation-state="rest"]', timeout=WAIT_MS
    )
    page.wait_for_selector('[data-approval-block][data-mode="gating-pending"]', timeout=WAIT_MS)

    def assert_contained() -> None:
        report = _width_report(page)
        assert report["document"] <= report["viewport"], report
        assert report["body"] <= report["viewport"], report
        ticket = report["ticket"]
        assert ticket is not None
        assert ticket["scrollWidth"] <= ticket["clientWidth"], report
        bad = [
            item
            for item in report["offenders"]
            if item["right"] > report["viewport"] + 1 and not item["localScroll"]
        ]
        assert not bad, bad

    for width in (280, 320, 390):
        page.set_viewport_size({"width": width, "height": 800})
        assert_contained()

    page.set_viewport_size({"width": 280, "height": 800})
    assert_contained()
    proposal_table = page.locator("[data-approval-block] .approval-draft .markdown table")
    proposal_table.wait_for(state="visible", timeout=WAIT_MS)
    assert proposal_table.evaluate("element => getComputedStyle(element).overflowX") == "auto"
    assert proposal_table.evaluate("element => element.scrollWidth > element.clientWidth")
    approval_actions = page.locator("[data-approval-block] .approval-actions")
    assert approval_actions.evaluate("element => element.scrollWidth <= element.clientWidth")

    page.click("[data-conversation-input]")
    page.wait_for_selector(
        '[data-conversation-pane][data-conversation-state="peeked"]', timeout=WAIT_MS
    )
    assert_contained()
    assert page.locator("[data-conversation-input]").is_visible()

    page.click("[data-conversation-expand]")
    page.wait_for_selector(
        '[data-conversation-pane][data-conversation-state="opened"]', timeout=WAIT_MS
    )
    assert_contained()
    assert page.locator("[data-conversation-input]").is_visible()

    page.set_viewport_size({"width": 844, "height": 390})
    assert_contained()

    page.set_viewport_size({"width": 1280, "height": 900})
    assert_contained()
    context.close()
