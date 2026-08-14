"""Focused frontend coverage for Stage ownership and execution route controls."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def test_ticket_takeover_is_quiet_and_current_stage_explains_user_ownership(
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
    page = open_page(context_factory(), server, f"#/workspace/{ticket_id}", ready)

    assert page.locator(".ticket-operating [data-execution-route]").count() == 0
    assert page.locator(".ticket-operating [data-stage-owner]").count() == 0
    assert page.locator(".ticket-operating [data-implementer]").count() == 0

    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert "execution_route" not in detail
    assert detail["stage_ownership_overrides"] == {}
    assert detail["default_stage_ownership_mode"] == "worker"
    assert detail["effective_stage_ownership_mode"] == "worker"
    page.click("[data-leash-face]")
    assert page.inner_text("[data-ticket-takeover-toggle]") == "Take over"


def test_release_from_a_default_user_stage_creates_a_worker_override(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    response = httpx.put(
        f"{server.base}/api/workers/coding/stages/needs_success/default-ownership",
        json={"ownership_mode": "user"},
        timeout=10.0,
    )
    assert response.status_code < 300, response.text
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Default user ownership",
    )["id"]
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/workspace/{ticket_id}", ready)

    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["stage_ownership_overrides"] == {}
    assert detail["effective_stage_ownership_mode"] == "user"
    page.click("[data-leash-face]")
    assert page.inner_text("[data-ticket-takeover-toggle]") == "Release"
    assert page.locator('[data-stage-run-label="you\'re on it"]').count() == 1

    with page.expect_response(
        lambda candidate: (
            candidate.request.method == "PUT"
            and f"/api/tickets/{ticket_id}/stage-ownership/needs_success" in candidate.url
        )
    ):
        page.click("[data-stage-release]")
    page.wait_for_selector("[data-stage-run-label]", state="detached", timeout=WAIT_MS)
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["stage_ownership_overrides"] == {"needs_success": "worker"}
    assert detail["effective_stage_ownership_mode"] == "worker"
    assert page.locator("[data-stage-run-label]").count() == 0
    if page.locator("details[data-leash]").get_attribute("open") is None:
        page.click("[data-leash-face]")
    assert page.inner_text("[data-ticket-takeover-toggle]") == "Take over"
