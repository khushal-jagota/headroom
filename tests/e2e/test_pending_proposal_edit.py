"""Browser proof that both proposal editors save the pending proposal itself."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, Request
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle


def _replace_editor_text(page: Page, selector: str, body: str) -> None:
    editor = page.locator(selector)
    editor.focus()
    editor.press("ControlOrMeta+A")
    editor.type(body)
    page.locator(f"{selector.rsplit(' ', 1)[0]} [data-accept]").focus()


def _wait_for_pending_body(page: Page, ticket_id: str, field: str, expected: str) -> None:
    page.wait_for_function(
        """async ({ticketId, field, expected}) => {
          const response = await fetch(`/api/tickets/${ticketId}`);
          if (!response.ok) return false;
          const ticket = await response.json();
          return ticket.pending_proposal?.field === field
            && ticket.pending_proposal?.body === expected;
        }""",
        arg={"ticketId": ticket_id, "field": field, "expected": expected},
        timeout=WAIT_MS,
    )


def test_pending_proposal_edits_persist_on_ticket_and_review_before_approval(
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
        "Edit pending proposal",
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Proposal awaits review.",
        ticket_id=ticket_id,
        stdin="Original proposal.",
    )
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    context = context_factory()
    ticket_ready = f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context, server, f"#/workspace/{ticket_id}", ticket_ready)
    ticket_editor = '[data-approval-block][data-field="success"] [data-edit]'
    _replace_editor_text(page, ticket_editor, "Saved from Ticket.")
    _wait_for_pending_body(page, ticket_id, "success", "Saved from Ticket.")

    review_card = f'[data-review-card][data-ticket-id="{ticket_id}"]'
    page.goto(f"{server.base}/#/review")
    page.wait_for_selector(review_card, timeout=WAIT_MS)
    page.reload()
    page.wait_for_selector(review_card, timeout=WAIT_MS)
    review_editor = f"{review_card} [data-edit]"
    page.locator(review_editor, has_text="Saved from Ticket.").wait_for(
        state="visible", timeout=WAIT_MS
    )
    write_requests: list[tuple[str, JsonObject]] = []

    def capture_write(request: Request) -> None:
        if request.method == "PUT" and f"/api/tickets/{ticket_id}/proposal" in request.url:
            payload = request.post_data_json
            assert payload is not None
            write_requests.append(("edit", payload))
        if request.method == "POST" and f"/api/tickets/{ticket_id}/accept/success" in request.url:
            payload = request.post_data_json
            assert payload is not None
            write_requests.append(("accept", payload))

    page.on("request", capture_write)
    approve_selector = f"{review_card} [data-accept]"
    page.wait_for_function(
        "selector => !document.querySelector(selector)?.disabled",
        arg=approve_selector,
        timeout=WAIT_MS,
    )
    editor = page.locator(review_editor)
    editor.focus()
    editor.press("ControlOrMeta+A")
    editor.type("Saved from Review.")
    page.click(approve_selector)
    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    assert [kind for kind, _payload in write_requests] == ["edit", "accept"]
    assert write_requests[0][1] == {"field": "success", "body": "Saved from Review."}
    assert "edited_body" not in write_requests[1][1]
    settled = api.get(server, f"/api/tickets/{ticket_id}")
    assert settled["pending_proposal"] is None
    assert settled["field_values"].get("success") == "Saved from Review."
