"""One live-browser proof for the Feedback boundary.

Unit and frontend tests own the transitions and presentation details. This test exists
because only a real browser plus the real server proves that global capture persists,
navigates to its canonical list, and follows the phone visual viewport above a keyboard.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Browser, BrowserContext, Page
from tests.e2e.harness import ApiHelper, ServerHandle

WAIT_MS = 10_000


def test_feedback_capture_persists_and_phone_sheet_tracks_the_visual_viewport(
    server: ServerHandle,
    browser: Browser,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    desktop = open_page(
        context_factory(), server, "#/day", '[data-feedback-trigger]'
    )
    trigger = desktop.locator("[data-feedback-trigger]")
    trigger.click()
    composer = desktop.locator("[data-feedback-input]")
    assert composer.evaluate("element => element === document.activeElement")
    assert desktop.locator(".fb-context .chip").inner_text() == "Home"
    composer.fill("Keep the feedback path close to the work.")
    desktop.keyboard.press("Enter")

    desktop.locator("[data-feedback-toast]", has_text="Saved to Feedback").wait_for(
        state="visible", timeout=WAIT_MS
    )
    saved = api.get(server, "/api/feedback")
    assert saved["open_count"] == 1
    assert saved["open"][0]["page_address"] == "#/day"
    assert saved["open"][0]["page_label"] == "Home"

    desktop.locator("[data-feedback-toast] a", has_text="View").click()
    desktop.wait_for_url("**/#/feedback", timeout=WAIT_MS)
    desktop.locator(
        "[data-feedback-note]", has_text="Keep the feedback path close to the work."
    ).wait_for(state="visible", timeout=WAIT_MS)

    phone_context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    phone_context.add_init_script(
        """
        (() => {
          const viewport = new EventTarget();
          Object.assign(viewport, { height: 844, offsetTop: 0, width: 390 });
          Object.defineProperty(window, "visualViewport", {
            configurable: true,
            value: viewport
          });
          window.__setFeedbackKeyboardHeight = (height) => {
            viewport.height = window.innerHeight - height;
            viewport.dispatchEvent(new Event("resize"));
          };
        })();
        """
    )
    try:
        phone = open_page(phone_context, server, "#/day", '[data-feedback-trigger]')
        phone.evaluate("window.__setFeedbackKeyboardHeight(291)")
        phone.locator("[data-feedback-trigger]").click()
        sheet = phone.locator("[data-feedback-capture]")
        sheet.wait_for(state="visible", timeout=WAIT_MS)
        assert phone.locator("[data-feedback-input]").get_attribute("enterkeyhint") == "done"
        trigger_box = phone.locator("[data-feedback-trigger]").bounding_box()
        assert trigger_box is not None
        assert trigger_box["width"] >= 40
        sheet_box = sheet.bounding_box()
        assert sheet_box is not None
        assert sheet_box["y"] + sheet_box["height"] <= 844 - 291 + 1
    finally:
        phone_context.close()
