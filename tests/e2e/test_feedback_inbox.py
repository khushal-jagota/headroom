"""One live-browser proof for the Feedback boundary.

Unit and frontend tests own the transitions and presentation details. This test exists
because only a real browser plus the real server proves that global capture persists,
and navigates to its canonical list. The frontend host owns viewport behavior.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ApiHelper, ServerHandle

WAIT_MS = 10_000


def test_feedback_capture_persists_and_opens_its_canonical_list(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    desktop = open_page(
        context_factory(), server, "#/workspace/chief-of-staff", "[data-feedback-trigger]"
    )
    trigger = desktop.locator("[data-feedback-trigger]")
    trigger.click()
    composer = desktop.locator("[data-feedback-input]")
    composer.fill("Keep the feedback path close to the work.")
    desktop.keyboard.press("Enter")

    desktop.locator("[data-feedback-toast]", has_text="Saved to Feedback").wait_for(
        state="visible", timeout=WAIT_MS
    )
    saved = api.get(server, "/api/feedback")
    assert saved["open_count"] == 1
    assert saved["open"][0]["page_address"] == "#/workspace/chief-of-staff"
    assert saved["open"][0]["page_label"] == "Workspace"

    desktop.locator("[data-feedback-toast] a", has_text="View").click()
    desktop.wait_for_url("**/#/feedback", timeout=WAIT_MS)
    desktop.locator(
        "[data-feedback-note]", has_text="Keep the feedback path close to the work."
    ).wait_for(state="visible", timeout=WAIT_MS)
