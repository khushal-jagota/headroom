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
        {
            "backend_key": "claude",
            "installed": True,
            "executable_path": "/fixture/claude",
            "version": "3.0.0",
            "identity": {
                "status": "authenticated",
                "account_label": "claude-fixture@example.com",
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


def test_ordinary_catalogue_refresh_cannot_be_started_twice(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    refresh_requests = 0
    pending_refreshes: list[Route] = []

    def backend_fixture(route: Route) -> None:
        nonlocal refresh_requests
        if route.request.url.endswith("/backends?refresh=true"):
            refresh_requests += 1
            pending_refreshes.append(route)
        else:
            route.fulfill(json=BACKENDS)

    page.route("**/api/conversation/backends**", backend_fixture)
    page.goto(server.base + "/#/backends")
    page.wait_for_selector('[data-conversation-backend="codex"]', timeout=WAIT_MS)

    refresh = page.locator("[data-backends-refresh]")
    refresh.click()
    page.wait_for_function("() => window.location.hash === '#/backends'", timeout=WAIT_MS)
    assert refresh.is_disabled()
    assert refresh.inner_text() == "Looking…"

    # Even a direct DOM press cannot start another read while the first is unresolved.
    refresh.evaluate("(button) => button.click()")
    assert refresh_requests == 1

    pending_refreshes.pop().fulfill(json=BACKENDS)
    refresh.wait_for(state="visible", timeout=WAIT_MS)
    page.wait_for_function(
        "() => !document.querySelector('[data-backends-refresh]').disabled",
        timeout=WAIT_MS,
    )
    assert refresh.inner_text() == "Look again"


def test_provider_usage_presses_are_serialized_and_results_stay_isolated(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    usage_requests: dict[str, int] = {"codex": 0, "claude": 0}
    pending_codex: list[Route] = []

    def backend_fixture(route: Route) -> None:
        if route.request.url.endswith("/codex/usage-refresh"):
            usage_requests["codex"] += 1
            pending_codex.append(route)
        elif route.request.url.endswith("/claude/usage-refresh"):
            usage_requests["claude"] += 1
            if usage_requests["claude"] == 1:
                route.fulfill(
                    json={
                        "backend_key": "claude",
                        "outcome": "failed",
                        "detail": "Claude usage could not be read.",
                        "observed_at": None,
                        "windows": [],
                    }
                )
            else:
                route.fulfill(
                    json={
                        "backend_key": "claude",
                        "outcome": "succeeded",
                        "detail": None,
                        "observed_at": "2026-07-31T12:35:56Z",
                        "windows": [
                            {
                                "name": "session",
                                "used_percent": 41.5,
                                "resets_at": "2026-08-01T12:35:56Z",
                            }
                        ],
                    }
                )
        else:
            route.fulfill(json=BACKENDS)

    page.route("**/api/conversation/backends**", backend_fixture)
    page.goto(server.base + "/#/backends")
    page.wait_for_selector('[data-conversation-backend="claude"]', timeout=WAIT_MS)

    codex_refresh = page.locator('[data-conversation-backend-usage-refresh="codex"]')
    codex_refresh.click()
    assert codex_refresh.is_disabled()
    codex_refresh.evaluate("(button) => button.click()")
    assert usage_requests["codex"] == 1

    page.locator('[data-conversation-backend-usage-refresh="claude"]').click()
    page.wait_for_selector(
        '[data-conversation-backend="claude"] [data-conversation-backend-usage="failed"]',
        timeout=WAIT_MS,
    )
    assert usage_requests["claude"] == 1

    pending_codex.pop().fulfill(
        json={
            "backend_key": "codex",
            "outcome": "succeeded",
            "detail": None,
            "observed_at": "2026-07-31T12:34:56Z",
            "windows": [
                {
                    "name": "weekly",
                    "used_percent": 27.0,
                    "resets_at": "2026-08-01T12:34:56Z",
                }
            ],
        }
    )
    page.wait_for_selector(
        '[data-conversation-backend="codex"] [data-conversation-backend-usage="succeeded"]',
        timeout=WAIT_MS,
    )
    assert "27% used" in page.locator(
        '[data-conversation-backend="codex"] [data-conversation-backend-usage="succeeded"]'
    ).inner_text()
    assert "Claude usage could not be read." in page.locator(
        '[data-conversation-backend="claude"] [data-conversation-backend-usage="failed"]'
    ).inner_text()

    page.locator('[data-conversation-backend-usage-refresh="claude"]').click()
    page.wait_for_selector(
        '[data-conversation-backend="claude"] '
        '[data-conversation-backend-usage="succeeded"]',
        timeout=WAIT_MS,
    )
    assert usage_requests["claude"] == 2
    assert "41.5% used" in page.locator(
        '[data-conversation-backend="claude"] '
        '[data-conversation-backend-usage="succeeded"]'
    ).inner_text()
    assert "27% used" in page.locator(
        '[data-conversation-backend="codex"] [data-conversation-backend-usage="succeeded"]'
    ).inner_text()


def test_unavailable_usage_and_not_installed_backend_are_plainly_rendered(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    backends = copy.deepcopy(BACKENDS)
    codex = backends["backends"][1]
    codex.update(
        installed=False,
        executable_path=None,
        version=None,
        identity=None,
        update_advisory=None,
        diagnoses=["Codex was not found on PATH."],
    )

    def backend_fixture(route: Route) -> None:
        if route.request.url.endswith("/claude/usage-refresh"):
            route.fulfill(
                json={
                    "backend_key": "claude",
                    "outcome": "unavailable",
                    "detail": "Claude does not report usage for this account.",
                    "observed_at": None,
                    "windows": [],
                }
            )
        else:
            route.fulfill(json=backends)

    page.route("**/api/conversation/backends**", backend_fixture)
    page.goto(server.base + "/#/backends")
    page.wait_for_selector('[data-conversation-backend="codex"]', timeout=WAIT_MS)

    codex_card = page.locator('[data-conversation-backend="codex"]')
    assert "not installed" in codex_card.inner_text()
    assert "Codex was not found on PATH." in codex_card.inner_text()
    assert codex_card.locator("[data-conversation-backend-usage-refresh]").count() == 0

    page.locator('[data-conversation-backend-usage-refresh="claude"]').click()
    unavailable = page.locator(
        '[data-conversation-backend="claude"] '
        '[data-conversation-backend-usage="unavailable"]'
    )
    unavailable.wait_for(state="visible", timeout=WAIT_MS)
    assert unavailable.inner_text() == "Claude does not report usage for this account."


def test_mobile_more_navigation_opens_the_backends_route(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    page.route(
        "**/api/conversation/backends**", lambda route: route.fulfill(json=BACKENDS)
    )

    page.goto(server.base + "/#/day")
    page.locator('[data-screen="more"]').click()
    page.locator('[data-shell-more-menu] a[href="#/backends"]').click()

    page.wait_for_selector('[data-conversation-backend="claude"]', timeout=WAIT_MS)
    assert page.evaluate("window.location.hash") == "#/backends"
    assert page.locator("[data-shell-screen-title]").inner_text() == "Backends"
    assert page.locator('[data-screen="backends"]').is_visible()
