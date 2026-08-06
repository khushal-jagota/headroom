"""PWA registration and the data-backed notification settings screen."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, ServerHandle


def test_manifest_and_push_only_worker_are_live_from_the_served_app(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    api: ApiHelper,
) -> None:
    api.direct_put(
        server,
        "/api/notifications/preferences/tickets/worker_failed",
        {"enabled": False},
    )
    api.direct_put(
        server,
        "/api/notifications/preferences/chief_of_staff/worker_failed",
        {"enabled": True},
    )
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

    assert page.locator("[data-notification-cell]").count() == 9
    assert page.locator('[data-notification-subject="tickets"] input').count() == 5
    assert page.locator('[data-notification-subject="chief_of_staff"] input').count() == 4

    chief_failed = page.locator('[data-notification-cell="chief_of_staff:worker_failed"] input')
    assert chief_failed.is_checked()
    with page.expect_response(
        lambda response: (
            response.request.method == "PUT"
            and response.url.endswith("/api/notifications/preferences/chief_of_staff/worker_failed")
        ),
        timeout=WAIT_MS,
    ) as response_info:
        chief_failed.uncheck()
    assert response_info.value.status == 200

    def stored_cells() -> tuple[int, int]:
        with sqlite3.connect(server.db_path) as conn:
            rows = dict(
                conn.execute(
                    "SELECT subject_key, enabled FROM notification_preferences "
                    "WHERE notification_type = 'worker_failed'"
                ).fetchall()
            )
        return int(rows["tickets"]), int(rows["chief_of_staff"])

    assert stored_cells() == (0, 0)
