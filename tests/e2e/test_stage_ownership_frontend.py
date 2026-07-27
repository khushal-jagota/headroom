"""Focused frontend coverage for Stage ownership and execution route controls."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page, ViewportSize
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def _put_stage_owner(
    server: ServerHandle, ticket_id: str, stage: str, mode: str | None
) -> JsonObject:
    response = httpx.put(
        f"{server.base}/api/tickets/{ticket_id}/stage-ownership/{stage}",
        json={"ownership_mode": mode},
        timeout=10.0,
    )
    assert response.status_code < 300, response.text
    body: JsonObject = response.json()
    return body


def test_ticket_facts_edit_current_stage_owner_without_execution_route(
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

    owner = ".ticket-facts [data-stage-owner]"
    assert page.locator(".ticket-facts [data-execution-route]").count() == 0
    assert page.locator(owner).count() == 1
    assert page.locator(".ticket-facts [data-implementer]").count() == 0

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


def test_workspace_stage_mark_renders_paired_on_desktop_and_mobile(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
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
        "Paired workspace ticket",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})
    detail = _put_stage_owner(server, ticket_id, "needs_success", "paired")
    assert detail["ticket_status"] == "empty"
    # Automatic opening is covered through the real runner in the new-worker public flow.
    # This test isolates rendering of the post-opening paired resting state.
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status = 'paired', conversation_id = ? "
            "WHERE id = ?",
            ("paired-render-session", ticket_id),
        )

    for viewport in (
        ViewportSize(width=1440, height=900),
        ViewportSize(width=390, height=844),
    ):
        page = context_factory().new_page()
        page.set_viewport_size(viewport)
        page.goto(server.base + "/#/workspace")
        page.wait_for_selector(f'[data-card][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
        page.wait_for_function(
            "() => window.__plannerDebug && window.__plannerDebug.sseOpens >= 1",
            timeout=WAIT_MS,
        )
        card = f'[data-card][data-ticket-id="{ticket_id}"]'
        page.wait_for_selector(card, timeout=WAIT_MS)
        assert page.locator('[aria-label="Ticket status"]').count() == 0
        assert page.get_attribute(card, "data-ticket-status") == "paired"
        paired_bucket = '[data-bucket-section][data-bucket-key="paired"]'
        assert page.inner_text(f"{paired_bucket} > summary .board-workspace-bucket-label") == (
            "Paired"
        )
        assert page.locator(f"{paired_bucket} {card}").count() == 1
        assert page.inner_text(f"{card} .list-row-title") == "Paired workspace ticket"
        assert page.locator(f"{card} > *").count() == 2
        paired_mark = page.locator(
            f'{card} .board-workspace-stage-mark[data-stage-state="upcoming"]'
        )
        assert paired_mark.count() == 1
        assert paired_mark.get_attribute("data-agent-working") == "false"
        assert paired_mark.get_attribute("data-latest-turn-ended") == "0"
