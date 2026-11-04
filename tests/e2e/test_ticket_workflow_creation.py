"""One connected proof of custom workflow form → API → saved Ticket display.

The existing real-server/browser fixtures cover serialization and navigation together.
Unit tests cannot prove the assembled form reaches the canonical writer and inspector.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ServerHandle, capture_success


def test_adapted_preset_creates_an_independent_custom_workflow(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    page = open_page(context_factory(), server, "#/workspace", "[data-new-ticket]")
    page.locator("[data-new-ticket]").click()
    page.locator("[data-create-title]").fill("Review the sample records")
    page.get_by_label("Brief", exact=True).fill(
        "Compare the supplied records and record the result."
    )
    page.locator("[data-create-preset]").select_option("coding")
    page.get_by_role("button", name="Add stage", exact=False).click()
    # A new stage sits before Consequences.
    names = page.get_by_label("Stage name", exact=True)
    names.nth(3).fill("Evidence comparison")
    custom_stage = page.locator(".workflow-stage").nth(3)
    custom_stage.get_by_label("Result", exact=True).fill("Comparison result")
    custom_stage.get_by_label("Instructions", exact=True).fill(
        "Compare only the two supplied records."
    )
    page.get_by_role("button", name="Review Ticket", exact=False).click()
    expect(page.locator("[data-create-ticket-review]")).to_contain_text(
        "Evidence comparison", timeout=WAIT_MS
    )
    expect(page.locator("[data-create-ticket-review]")).to_contain_text(
        "Compare only the two supplied records."
    )
    capture_success(page, "custom-workflow-review.png")
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith("/api/tickets")
        ),
        timeout=WAIT_MS,
    ) as submitted:
        page.locator("[data-create-ticket-submit]").click()
    response = submitted.value
    assert response.ok, response.text()
    saved = response.json()
    assert saved["workflow"]["worker_profile_id"] is None
    assert any(
        step["label"] == "Evidence comparison"
        and step["instructions"] == "Compare only the two supplied records."
        for step in saved["workflow"]["stages"]
    )
    page.wait_for_selector(
        f'[data-screen="ticket"][data-ticket-id="{saved["id"]}"]', timeout=WAIT_MS
    )
    page.locator("[data-stage-fold-open]").click()
    expect(page.get_by_text("Comparison result", exact=True).first).to_be_visible(timeout=WAIT_MS)
    page.get_by_text("Comparison result", exact=True).first.scroll_into_view_if_needed()
    capture_success(page, "custom-workflow-ticket.png")
    read = httpx.get(
        server.base + "/api/tickets", params={"id": saved["id"], "detail": "full"}, timeout=10
    )
    assert read.is_success, read.text
    assert read.json()["id"] == saved["id"]
    assert read.json()["workflow"] == saved["workflow"]
