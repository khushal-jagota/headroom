"""A retained installed-app page learns that its same-origin server changed release."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext

from tests.e2e.harness import WAIT_MS, ServerHandle


def test_retained_page_offers_and_applies_same_origin_release_update(
    server_factory: Callable[..., ServerHandle],
    stop_server: Callable[[ServerHandle], None],
    restart_server: Callable[..., ServerHandle],
    context_factory: Callable[[], BrowserContext],
) -> None:
    first_sha = "1" * 40
    next_sha = "2" * 40
    first_server = server_factory(app_sha=first_sha)
    page = context_factory().new_page()
    page.goto(first_server.base + "/#/day")
    page.locator("[data-release-update]").wait_for(state="detached", timeout=WAIT_MS)
    assert page.locator('meta[name="panels-app-sha"]').get_attribute("content") == first_sha

    stop_server(first_server)
    replacement = restart_server(first_server, app_sha=next_sha)
    assert replacement.base == first_server.base

    # This is the retained-PWA boundary: the old document resumes against a new
    # process on the same origin. Lower test layers cannot retain that document.
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    update = page.locator("[data-release-update]")
    update.wait_for(state="visible", timeout=WAIT_MS)
    assert page.locator('meta[name="panels-app-sha"]').get_attribute("content") == first_sha

    update.get_by_role("button", name="Update now").click()
    page.wait_for_function(
        "sha => document.querySelector('meta[name=\"panels-app-sha\"]')?.content === sha",
        arg=next_sha,
        timeout=WAIT_MS,
    )
    page.locator("[data-release-update]").wait_for(state="detached", timeout=WAIT_MS)
