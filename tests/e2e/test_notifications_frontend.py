"""PWA registration and the data-backed notification settings screen."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ServerHandle


def test_manifest_and_push_only_worker_are_live_from_the_served_app(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
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
