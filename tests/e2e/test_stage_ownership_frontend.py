"""Focused frontend coverage for Stage ownership and execution route controls."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def test_ticket_operating_line_edits_current_stage_owner_without_execution_route(
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
        "Stage owner controls",
    )["id"]
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/ticket/{ticket_id}", ready)

    owner = ".ticket-operating [data-stage-owner]"
    assert page.locator(".ticket-operating [data-execution-route]").count() == 0
    assert page.locator(owner).count() == 1
    assert page.locator(".ticket-operating [data-implementer]").count() == 0

    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert "execution_route" not in detail
    assert detail["stage_ownership_overrides"] == {}
    assert detail["default_stage_ownership_mode"] == "worker"
    assert detail["effective_stage_ownership_mode"] == "worker"
    assert page.get_attribute(owner, "data-owner-mode") == "default"
    assert page.get_attribute(owner, "data-default-owner") == "worker"
    assert page.get_attribute(owner, "data-effective-owner") == "worker"
    assert page.inner_text("[data-ticket-takeover-toggle]") == "Take over"

    with page.expect_response(
        lambda response: (
            response.request.method == "PUT"
            and f"/api/tickets/{ticket_id}/stage-ownership/" in response.url
        )
    ):
        page.select_option(f"{owner} select", "user")
    page.wait_for_function(
        """({ selector }) => document.querySelector(selector)?.dataset.ownerMode === 'user'""",
        arg={"selector": owner},
        timeout=WAIT_MS,
    )
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["stage_ownership_overrides"][detail["stage"]] == "user"
    assert detail["ticket_status"] == "user"
    assert page.inner_text("[data-ticket-takeover-toggle]") == "Release"

    with page.expect_response(
        lambda response: (
            response.request.method == "PUT"
            and f"/api/tickets/{ticket_id}/stage-ownership/" in response.url
        )
    ):
        page.select_option(f"{owner} select", "")
    page.wait_for_function(
        """({ selector }) => document.querySelector(selector)?.dataset.ownerMode === 'default'""",
        arg={"selector": owner},
        timeout=WAIT_MS,
    )
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["stage_ownership_overrides"] == {}
    assert detail["effective_stage_ownership_mode"] == "worker"
    assert page.inner_text("[data-ticket-takeover-toggle]") == "Take over"
