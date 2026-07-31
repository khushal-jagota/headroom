"""Backends screen behavior with provider interaction fully browser-fixtured."""

from __future__ import annotations

import copy
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Route
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ServerHandle

BACKENDS = {
    "backends": [
        {
            "backend_key": "hermes",
            "installed": True,
            "executable_path": "/fixture/hermes",
            "version": "1.0.0",
            "identity": None,
            "available_models": [],
            "reasoning_effort_options": [],
            "update_advisory": None,
            "diagnoses": [],
        },
        {
            "backend_key": "codex",
            "installed": True,
            "executable_path": "/fixture/codex",
            "version": "2.0.0",
            "identity": {
                "status": "authenticated",
                "account_label": "fixture@example.com",
                "detail": None,
                "login_command": None,
            },
            "available_models": [],
            "reasoning_effort_options": [],
            "update_advisory": None,
            "diagnoses": [],
        },
    ]
}


def test_usage_is_absent_on_mount_and_acquired_only_by_its_button(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    usage_requests = 0

    def backend_fixture(route: Route) -> None:
        nonlocal usage_requests
        if route.request.url.endswith("/usage-refresh"):
            usage_requests += 1
            route.fulfill(
                json={
                    "backend_key": "codex",
                    "outcome": "succeeded",
                    "detail": None,
                    "observed_at": "2026-07-31T12:34:56Z",
                    "windows": [
                        {
                            "name": "5 hours",
                            "used_percent": 12.5,
                            "resets_at": "2026-07-31T15:00:00Z",
                        }
                    ],
                }
            )
        else:
            route.fulfill(json=BACKENDS)

    page.route("**/api/conversation/backends**", backend_fixture)
    page.goto(server.base + "/#/backends")
    page.wait_for_selector('[data-conversation-backend="codex"]', timeout=WAIT_MS)

    assert usage_requests == 0
    assert (
        page.locator(
            '[data-conversation-backend="hermes"] '
            "[data-conversation-backend-usage-refresh]"
        ).count()
        == 0
    )

    page.locator('[data-conversation-backend-usage-refresh="codex"]').click()
    page.wait_for_selector(
        '[data-conversation-backend-usage="succeeded"]', timeout=WAIT_MS
    )

    assert usage_requests == 1
    assert "12.5% used" in page.locator(
        '[data-conversation-backend-usage="succeeded"]'
    ).inner_text()


def test_a_usage_failure_does_not_take_away_backend_maintenance(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    backends = copy.deepcopy(BACKENDS)
    codex = backends["backends"][1]
    codex["update_advisory"] = {
        "install_method": "native",
        "update_command": "codex update",
        "latest_version": "2.1.0",
        "update_available": True,
        "detail": "Version 2.1.0 is available.",
    }
    update_requests = 0

    def backend_fixture(route: Route) -> None:
        nonlocal update_requests
        if route.request.url.endswith("/usage-refresh"):
            route.fulfill(
                json={
                    "backend_key": "codex",
                    "outcome": "unauthenticated",
                    "detail": "Codex is not logged in.",
                    "observed_at": None,
                    "windows": [],
                }
            )
        elif route.request.url.endswith("/update"):
            update_requests += 1
            route.fulfill(
                json={
                    "outcome": "succeeded",
                    "detail": "Codex was updated to 2.1.0.",
                    "output_tail": "",
                }
            )
        else:
            route.fulfill(json=backends)

    page.route("**/api/conversation/backends**", backend_fixture)
    page.goto(server.base + "/#/backends")
    page.wait_for_selector('[data-conversation-backend="codex"]', timeout=WAIT_MS)

    page.locator('[data-conversation-backend-usage-refresh="codex"]').click()
    page.wait_for_selector(
        '[data-conversation-backend-usage="unauthenticated"]', timeout=WAIT_MS
    )
    assert page.locator('[data-conversation-backend-update="codex"]').is_enabled()

    page.locator('[data-conversation-backend-update="codex"]').click()
    page.wait_for_selector(
        '[data-conversation-backend-result="succeeded"]', timeout=WAIT_MS
    )
    assert update_requests == 1
    assert "Codex is not logged in." in page.locator(
        '[data-conversation-backend-usage="unauthenticated"]'
    ).inner_text()
