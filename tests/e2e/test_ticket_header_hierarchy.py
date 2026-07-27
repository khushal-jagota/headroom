"""The Ticket header keeps identity, operating state, and planning facts distinct."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import pytest
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def _create_ticket(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    title: str,
) -> str:
    return str(
        cli(
            server,
            "ticket",
            "create",
            "--worker-type",
            "coding",
            "--title",
            title,
        )["id"]
    )


def _set_header_state(
    server: ServerHandle,
    ticket_id: str,
    *,
    stage: str = "needs_success",
    ticket_status: str = "agent",
    priority: str = "P3",
) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET stage = ?, ticket_status = ?, priority = ? WHERE id = ?",
            (stage, ticket_status, priority, ticket_id),
        )


def test_ticket_header_uses_clean_split_and_keeps_controls_live(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = _create_ticket(server, cli, "Clean split header")
    _set_header_state(server, ticket_id)
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/ticket/{ticket_id}", ready)

    assert page.locator(".ticket-title-row [data-copy]").count() == 1
    assert page.locator(".ticket-operating [data-ticket-status='agent']").count() == 1
    assert (
        page.inner_text(".ticket-operating [data-ticket-status='agent']")
        == "agent working"
    )
    assert page.locator(".ticket-operating [data-stage-owner]").count() == 1
    assert page.locator(".ticket-operating [data-ticket-takeover-toggle]").count() == 1
    assert page.locator(".ticket-planning [data-priority-control]").count() == 1
    assert page.inner_text(".ticket-planning [data-deadline-control]") == "+ due"
    project_lines = page.inner_text(
        ".ticket-planning [data-project-control] .pill"
    ).splitlines()
    sprint_lines = page.inner_text(
        ".ticket-planning [data-sprint-control] .pill"
    ).splitlines()
    assert project_lines[0] == "+ project"
    assert sprint_lines[0] == "+ sprint"
    assert page.locator(".ticket-head [data-worker-type]").count() == 0
    assert page.locator(".ticket-head [data-marker]").count() == 0

    operating_box = page.locator(".ticket-operating").bounding_box()
    planning_box = page.locator(".ticket-planning").bounding_box()
    assert operating_box is not None
    assert planning_box is not None
    assert operating_box["y"] < planning_box["y"]

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith(f"/api/tickets/{ticket_id}")
    ):
        page.select_option("[data-priority-control] select", "P1")
    page.wait_for_selector('[data-priority-alert="P1"]', timeout=WAIT_MS)
    assert page.inner_text('[data-priority-alert="P1"]').splitlines()[0] == "P1 · URGENT"
    assert page.locator("[data-priority-control]").count() == 0

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith(f"/api/tickets/{ticket_id}")
    ):
        page.select_option('[data-priority-alert="P1"] select', "P2")
    page.wait_for_selector("[data-priority-control]", timeout=WAIT_MS)
    assert page.locator("[data-priority-alert]").count() == 0

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith(f"/api/tickets/{ticket_id}")
    ):
        page.fill("[data-deadline]", "2026-08-02")
    page.wait_for_function(
        """() => document.querySelector('[data-deadline-control]')
            ?.textContent?.includes('2026-08-02')""",
        timeout=WAIT_MS,
    )
    assert api.get(server, f"/api/tickets/{ticket_id}")["deadline"] == "2026-08-02"

    page.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin=server.base,
    )
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/tickets/{ticket_id}/copy-text")
    ):
        page.click(".ticket-title-row [data-copy]")
    page.wait_for_function(
        """() => document.querySelector('.ticket-title-row [data-copy]')
            ?.textContent?.trim() === 'Copied'""",
        timeout=WAIT_MS,
    )

    page.set_viewport_size({"width": 390, "height": 844})
    header_box = page.locator(".ticket-head").bounding_box()
    assert header_box is not None
    for selector in (".ticket-title-row", ".ticket-operating", ".ticket-planning"):
        box = page.locator(selector).bounding_box()
        assert box is not None
        assert box["x"] >= header_box["x"]
        assert box["x"] + box["width"] <= header_box["x"] + header_box["width"] + 1


@pytest.mark.parametrize(
    ("stage", "ticket_status", "expected_key", "expected_text", "expected_tone"),
    [
        ("needs_success", "agent", "agent", "agent working", None),
        (
            "needs_success",
            "awaiting_approval",
            "awaiting_approval",
            "awaiting approval",
            "attention",
        ),
        ("needs_success", "needs_user", "needs_user", "needs user", "attention"),
        ("needs_success", "blocked", "blocked", "blocked", "error"),
        ("needs_success", "errored", "errored", "errored", "error"),
        ("done", "empty", "done", "done", "done"),
    ],
)
def test_ticket_operating_line_gives_each_state_one_clear_treatment(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    stage: str,
    ticket_status: str,
    expected_key: str,
    expected_text: str,
    expected_tone: str | None,
) -> None:
    ticket_id = _create_ticket(server, cli, f"{expected_text} header")
    _set_header_state(
        server,
        ticket_id,
        stage=stage,
        ticket_status=ticket_status,
    )
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/ticket/{ticket_id}", ready)
    state = page.locator(f'[data-ticket-status="{expected_key}"]')

    assert state.count() == 1
    assert state.inner_text() == expected_text
    assert state.get_attribute("class") is not None
    classes = set((state.get_attribute("class") or "").split())
    assert ("ticket-status-display--attention" in classes) == (
        expected_tone == "attention"
    )
    assert ("ticket-status-display--error" in classes) == (expected_tone == "error")
    assert ("ticket-status-display--done" in classes) == (expected_tone == "done")
    assert page.locator(".ticket-operating [data-marker]").count() == 0

    if stage == "done":
        assert page.locator(".ticket-operating [data-stage-owner]").count() == 0
        assert (
            page.locator(".ticket-operating [data-ticket-takeover-toggle]").count() == 0
        )


@pytest.mark.parametrize("priority", ["P0", "P1"])
def test_exceptional_priority_appears_once_above_the_title(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    priority: str,
) -> None:
    ticket_id = _create_ticket(server, cli, f"{priority} header")
    _set_header_state(server, ticket_id, priority=priority)
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/ticket/{ticket_id}", ready)

    alert = page.locator(f'[data-priority-alert="{priority}"]')
    title = page.locator(".ticket-title-row")
    assert alert.count() == 1
    assert page.locator("[data-priority-control]").count() == 0
    alert_box = alert.bounding_box()
    title_box = title.bounding_box()
    assert alert_box is not None
    assert title_box is not None
    assert alert_box["y"] < title_box["y"]
