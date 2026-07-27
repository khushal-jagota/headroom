"""Focused browser proof for the manually fetched VPS-status popover."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.harness import WAIT_MS, ServerHandle


def _payload(state: str) -> dict[str, object]:
    section = {"state": state, "summary": f"{state} evidence"}
    return {
        "collected_at": "2026-07-24T00:00:00+00:00",
        "overall_state": state,
        "environment": section,
        "release": section,
        "backup": section,
        "disk": section,
        "workloads": {**section, "items": []},
        "worktrees": {**section, "items": []},
        "logs": {**section, "file_count": 0, "total_bytes": 0},
        "cleanup_candidates": {**section, "candidates": []},
        "resources": {
            **section,
            "cpu_percent": None,
            "load_averages": None,
            "ram": None,
            "swap": None,
        },
    }


def _wait_for_status_label(page: Page, label: str) -> None:
    page.wait_for_function(
        "label => document.querySelector('[data-vps-status-content]')"
        "?.textContent?.includes(label)",
        arg=label,
        timeout=WAIT_MS,
    )


def test_header_status_popover_is_manual_and_renders_status_states(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    requests: list[str] = []
    payloads = [
        _payload("healthy"),
        _payload("warning"),
        _payload("unavailable"),
        _payload("review_needed"),
    ]

    def fulfill(route: Route) -> None:
        requests.append(route.request.url)
        route.fulfill(json=payloads[min(len(requests) - 1, len(payloads) - 1)])

    page.route("**/api/vps-status", fulfill)
    page.goto(server.base + "/#/day")
    page.wait_for_selector("[data-vps-status]", timeout=WAIT_MS)
    assert requests == []

    page.locator("[data-vps-status] button").first.click()
    page.wait_for_selector("[data-vps-status-content]", timeout=WAIT_MS)
    assert "Healthy" in page.locator("[data-vps-status-content]").inner_text()
    refresh = page.get_by_role("button", name="Refresh")
    refresh.click()
    _wait_for_status_label(page, "Warning")
    refresh.click()
    _wait_for_status_label(page, "Unavailable")
    refresh.click()
    _wait_for_status_label(page, "Review needed")
    assert len(requests) == 4
