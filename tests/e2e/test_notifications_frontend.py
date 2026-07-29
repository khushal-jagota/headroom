"""PWA registration and the data-backed notification settings screen."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, expect
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ServerHandle


def test_notifications_are_catalogue_driven_and_persist(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    page.goto(server.base + "/#/notifications")
    page.wait_for_selector(
        '[data-screen="notifications"] [data-notification-type]', timeout=WAIT_MS
    )

    options = page.locator("[data-notification-type]")
    assert options.evaluate_all(
        "(nodes) => nodes.map((node) => node.dataset.notificationType)"
    ) == [
        "ticket_needs_approval",
        "ticket_needs_input",
        "permission_requested",
        "worker_completed",
        "worker_failed",
    ]
    completed = page.locator('[data-notification-type="worker_completed"] input')
    expect(completed).to_be_checked()
    completed.uncheck()
    expect(completed).not_to_be_checked()

    page.reload()
    page.wait_for_selector(
        '[data-notification-type="worker_completed"]', timeout=WAIT_MS
    )
    expect(page.locator('[data-notification-type="worker_completed"] input')).not_to_be_checked()


def test_manifest_and_push_only_worker_are_live_on_a_phone_viewport(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(server.base + "/#/notifications")
    page.wait_for_selector('[data-screen="notifications"]', timeout=WAIT_MS)
    page.wait_for_function(
        "() => document.querySelector('[data-device-state]')?.dataset.deviceState !== 'checking'",
        timeout=WAIT_MS,
    )

    assert page.locator('link[rel="manifest"]').get_attribute("href") == (
        "/static/manifest.webmanifest"
    )
    assert page.evaluate(
        """async () => {
          if (!('serviceWorker' in navigator)) return false;
          const registration = await navigator.serviceWorker.ready;
          return registration.scope === `${location.origin}/`;
        }"""
    )
    geometry = page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert geometry <= 0
