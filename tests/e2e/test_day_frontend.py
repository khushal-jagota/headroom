"""Browser contract for the Day daily-hub redesign."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ServerHandle


def _ticket(
    ticket_id: str,
    *,
    stage: str = "needs_implementation",
    status: str = "empty",
    is_done: bool = False,
    needs_me: bool = False,
    agent_working: bool = False,
) -> dict[str, Any]:
    return {
        "id": ticket_id,
        "title": ticket_id,
        "stage": stage,
        "ticket_status": status,
        "is_done": is_done,
        "waiting_to_closeout": False,
        "gating_field": "implementation",
        "conversation_id": None,
        "needs_me": needs_me,
        "agent_working": agent_working,
        "latest_turn_ended_sequence": 0,
    }


def _day_payload(tickets: list[dict[str, Any]], *, focus: str | None) -> dict[str, Any]:
    return {
        "id": "day_2026-07-04",
        "focus": focus,
        "if_today_lands": "The essential path works.",
        "brief_take": "A short line below the progress row.",
        "watchout": "Keep the work on the narrow path.",
        "midday_reconciliation": "This must not appear on Day.",
        "tickets": tickets,
    }


def _mock_day(page: Page, payload: dict[str, Any]) -> None:
    page.route(
        "**/api/day/today",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )


def _open_day(
    context: BrowserContext,
    server: ServerHandle,
    payload: dict[str, Any],
) -> Page:
    page = context.new_page()
    _mock_day(page, payload)
    page.goto(server.base + "/#/day")
    page.wait_for_selector('[data-screen="day"] [data-day-overview]', timeout=WAIT_MS)
    return page


def test_day_populated_state_shows_live_progress_and_action_tiles(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    payload = _day_payload(
        [
            _ticket("done", stage="done", is_done=True),
            _ticket("working", status="agent", agent_working=True),
            _ticket("paired", status="paired"),
            _ticket("review", status="awaiting_approval"),
            _ticket("needs-you", needs_me=True),
            _ticket("upcoming"),
        ],
        focus="A calm overview of the work",
    )
    page = _open_day(context_factory(), server, payload)

    assert page.locator('[data-day-state="populated"]').count() == 1
    assert page.locator("[data-day-focus]").inner_text() == "A calm overview of the work"
    assert page.locator("[data-day-lands]").inner_text().startswith("If today lands")
    assert page.locator("[data-day-ticket-dot]").count() == 6
    assert page.locator('[data-day-ticket-dot][data-stage-state="completed"]').count() == 1
    assert page.locator('[data-day-ticket-dot][data-stage-state="current-running"]').count() == 1
    assert page.locator('[data-day-ticket-dot][data-stage-state="current-paired"]').count() == 1
    assert page.locator(
        '[data-day-ticket-dot][data-stage-state="current-awaiting-approval"]'
    ).count() == 1
    assert page.locator('[data-day-ticket-dot][data-stage-state="needs-me"]').count() == 1
    assert page.locator('[data-day-ticket-dot][data-stage-state="upcoming"]').count() == 1

    actions = page.locator("[data-day-action]")
    assert actions.evaluate_all(
        "elements => elements.map(element => [element.dataset.dayAction, "
        "element.querySelector('.n').textContent.trim(), "
        "element.querySelector('.k').textContent.trim()])"
    ) == [
        ["needs-me", "1", "Need you"],
        ["review", "1", "To review"],
        ["working", "1", "Working"],
        ["paired", "1", "Paired"],
        ["done", "1", "Done"],
    ]
    assert page.locator('[data-day-action="needs-me"]').get_attribute("href") == "#/workspace"
    assert page.locator('[data-day-action="review"]').get_attribute("href") == "#/review"
    assert page.locator('[data-day-action="paired"]').get_attribute("href") == "#/workspace"
    assert page.locator('[data-day-action="needs-me"]').evaluate(
        """element => {
          const probe = document.createElement('span');
          probe.style.backgroundColor = getComputedStyle(document.documentElement)
            .getPropertyValue('--accent-bright').trim();
          document.body.appendChild(probe);
          const expected = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return getComputedStyle(element).backgroundColor === expected;
        }"""
    )
    assert page.locator("[data-day-watch] .lbl").inner_text() == "WATCH"
    assert page.locator("[data-day-watch] .v").inner_text() == "Keep the work on the narrow path."
    assert page.locator("[data-day-midday]").count() == 0
    assert page.locator(".day-grid, .review-entry").count() == 0

    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    assert page.locator('[data-day-action="needs-me"]').is_visible()


def test_day_calm_state_hides_the_need_you_tile(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    payload = _day_payload(
        [
            _ticket("done", stage="done", is_done=True),
            _ticket("working", status="agent", agent_working=True),
            _ticket("paired", status="paired"),
        ],
        focus="Nothing is waiting on you",
    )
    page = _open_day(context_factory(), server, payload)

    assert page.locator('[data-day-state="calm"]').count() == 1
    assert page.locator('[data-day-action="needs-me"]').count() == 0
    assert page.locator('[data-day-action="working"] .n').inner_text() == "1"
    assert page.locator('[data-day-action="working"] .k').inner_text() == "Working"
    assert page.locator('[data-day-action="paired"] .n').inner_text() == "1"
    assert page.locator('[data-day-action="paired"] .k').inner_text() == "Paired"
    assert page.locator('[data-day-action="done"] .n').inner_text() == "1"
    assert page.locator('[data-day-action="done"] .k').inner_text() == "Done"


def test_day_empty_state_has_only_the_plan_invitation(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = _open_day(
        context_factory(),
        server,
        _day_payload([], focus=None),
    )

    assert page.locator('[data-day-state="empty"]').count() == 1
    assert page.locator("[data-day-focus]").inner_text() == "No focus set yet"
    assert (
        page.locator("[data-day-empty-line]").inner_text()
        == "The day is empty — nothing planned."
    )
    assert page.locator("[data-day-plan]").get_attribute("href") == "#/day"
    assert page.locator("[data-day-dots], [data-day-actions], [data-day-watch]").count() == 0
