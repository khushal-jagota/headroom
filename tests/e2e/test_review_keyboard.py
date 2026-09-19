"""Real-browser proof for Review keyboard safety."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle


def _add_to_today(api: ApiHelper, server: ServerHandle, *ticket_ids: str) -> None:
    for ticket_id in ticket_ids:
        api.direct_put(server, f"/api/collections/day_tickets/today/{ticket_id}", {})


def _wait_enabled(page: Page, selector: str) -> None:
    page.wait_for_function(
        "sel => { const button = document.querySelector(sel);"
        " return !!button && !button.disabled; }",
        arg=selector,
        timeout=WAIT_MS,
    )


def test_review_shortcuts_do_not_escape_editable_controls(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tickets = []
    for index in ("one", "two"):
        ticket = cli(
            server,
            "ticket",
            "create",
            "--worker-type",
            "coding",
            "--title",
            f"Shortcut ticket {index}",
        )["id"]
        cli(
            server,
            "worker",
            "propose",
            "--recap",
            f"Recap {index}.",
            ticket_id=ticket,
            stdin=f"# Proposal {index}\n\n- item",
        )
        tickets.append(ticket)

    _add_to_today(api, server, *tickets)
    card = "[data-review-card]"
    page = open_page(context_factory(), server, "#/review", card)
    ticket_before = page.get_attribute(card, "data-ticket-id")

    approve_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            approve_requests.append(request.url)
            if request.method == "POST"
            and ("/accept/" in request.url or request.url.endswith("/approve"))
            else None
        ),
    )

    editor = page.locator(f"{card} [data-edit]")
    editor.focus()
    page.keyboard.press("Meta+Enter")
    page.evaluate("async () => { await (await fetch('/api/review')).text(); }")
    assert approve_requests == []
    assert all(
        api.get(server, f"/api/tickets/{ticket}")["pending_proposal"] is not None
        for ticket in tickets
    )
    assert page.get_attribute(card, "data-ticket-id") == ticket_before

    editor.focus()
    page.keyboard.type("o")
    assert page.url.endswith("#/review")

    page.locator(f"{card} [data-scope-ceiling]").focus()
    page.keyboard.press("s")
    page.keyboard.press("o")
    assert page.url.endswith("#/review")
    assert page.get_attribute(card, "data-ticket-id") == ticket_before

    page.locator(".review-keys").click()
    page.keyboard.press("s")
    page.wait_for_function(
        "prev => { const card = document.querySelector('[data-review-card]');"
        " return !!card && card.getAttribute('data-ticket-id') !== prev; }",
        arg=ticket_before,
        timeout=WAIT_MS,
    )

    _wait_enabled(page, f"{card} [data-accept]")
    approved_ticket = page.get_attribute(card, "data-ticket-id")
    page.locator(".review-keys").click()
    page.keyboard.press("Meta+Enter")
    page.wait_for_function(
        "prev => { const card = document.querySelector('[data-review-card]');"
        " return !card || card.getAttribute('data-ticket-id') !== prev; }",
        arg=approved_ticket,
        timeout=WAIT_MS,
    )
    approved = api.get(server, f"/api/tickets/{approved_ticket}")
    assert approved["pending_proposal"] is None
