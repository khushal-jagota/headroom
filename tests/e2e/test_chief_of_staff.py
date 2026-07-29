"""What the Chief's panel opens on before there is a conversation.

The rest of this file's browser coverage was retired when the verification tiers were
right-sized; what is kept is the one thing that had no proof before — a panel with no
conversation showing what a first message would actually run on.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.harness import ServerHandle

WAIT_MS = 10_000
MODEL_PICKER = "[data-conversation-picker-model]"


def test_the_chief_panel_opens_on_the_backend_it_is_configured_on(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """A panel with no conversation shows what a first message would actually run on.

    The Chief is moved off the backend it ships on, and the rail follows: it says what
    the Chief is configured on rather than what a backend does when nobody says.
    """
    moved = httpx.put(
        f"{server.base}/api/workers/chief-of-staff/launch-defaults",
        json={
            "employee_backend": "claude",
            "employee_launch_model": "sonnet",
            "employee_launch_reasoning_effort": None,
        },
        timeout=10.0,
    )
    assert moved.status_code < 300, moved.text

    page = open_page(
        context_factory(),
        server,
        "#/agents/chief-of-staff",
        'section[data-screen="agents"] [data-conversation-input]',
    )

    # The rail is drawn down the side of the model picker's panel, which is where a
    # person goes to see or change what the next message would start.
    page.click(f"{MODEL_PICKER} [data-conversation-picker-trigger]")
    page.wait_for_selector("[data-conversation-backend-showing]", timeout=WAIT_MS)
    assert page.inner_text("[data-conversation-backend-showing]") == "claude"
    # And the model beside it is the Chief's own, whether or not this machine has claude
    # installed to name it more prettily than the value itself.
    assert "sonnet" in page.inner_text(f"{MODEL_PICKER} .c2-pick-face").lower()


def test_agents_routes_follow_workspace_responsively_and_keep_navigation_active(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    page.set_viewport_size({"width": 1200, "height": 800})
    page.goto(server.base + "/#/agents")
    page.wait_for_selector("[data-agents-layout]", timeout=WAIT_MS)

    roster = page.locator(".agents-workspace-roster")
    conversation = page.locator(".agents-workspace-conversation")
    row = page.locator('[data-agent-id="chief-of-staff"]')
    agents_nav = page.locator('[data-screen="agents-nav"]')
    assert roster.is_visible()
    assert conversation.is_visible()
    assert row.is_visible()
    assert "active" in (agents_nav.get_attribute("class") or "").split()
    assert page.locator("[data-chief-conversation-loading]").count() == 0

    row.click()
    page.wait_for_url("**/#/agents/chief-of-staff", timeout=WAIT_MS)
    assert roster.is_visible()
    assert conversation.is_visible()
    assert row.get_attribute("aria-current") == "page"
    assert "active" in (agents_nav.get_attribute("class") or "").split()

    page.reload()
    page.wait_for_selector("[data-agents-layout]", timeout=WAIT_MS)
    assert page.locator(".agents-workspace-roster").is_visible()
    assert page.locator(".agents-workspace-conversation").is_visible()

    page.set_viewport_size({"width": 390, "height": 700})
    assert not page.locator(".agents-workspace-roster").is_visible()
    assert page.locator(".agents-workspace-conversation").is_visible()
    back = page.locator(".agents-conversation-back")
    assert back.is_visible()
    assert back.get_attribute("href") == "#/agents"
    assert back.get_attribute("aria-label") == "Back to agents"

    back.click()
    page.wait_for_url("**/#/agents", timeout=WAIT_MS)
    page.wait_for_selector(".agents-workspace-roster", state="visible", timeout=WAIT_MS)
    page.wait_for_selector(
        ".agents-workspace-conversation", state="hidden", timeout=WAIT_MS
    )

    page.go_back()
    page.wait_for_url("**/#/agents/chief-of-staff", timeout=WAIT_MS)
    assert page.locator(".agents-workspace-conversation").is_visible()


def test_unknown_agent_route_remains_unknown(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    page.goto(server.base + "/#/agents/not-an-agent")
    page.wait_for_selector(".quiet-line", timeout=WAIT_MS)
    assert page.locator(".quiet-line").inner_text() == "no such screen"
    assert page.url.endswith("/#/agents/not-an-agent")


def test_chief_lookup_failure_is_visible_and_retryable(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    attempts = 0
    allow_success = False

    def answer_lookup(route: Route) -> None:
        nonlocal attempts, allow_success
        if not route.request.url.endswith("/api/chief/conversation"):
            route.continue_()
            return
        attempts += 1
        if not allow_success:
            route.fulfill(
                status=503,
                content_type="application/json",
                body='{"error":{"message":"conversation lookup unavailable"}}',
            )
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"conversation_id":null}',
            )

    page.route("**/api/chief/conversation*", answer_lookup)
    page.goto(server.base + "/#/agents/chief-of-staff")
    page.wait_for_selector("[data-chief-conversation-error]", timeout=WAIT_MS)
    assert "HTTP 503" in page.locator("[data-chief-conversation-error]").inner_text()
    retry = page.locator("[data-chief-conversation-retry]")
    assert retry.is_visible()
    allow_success = True
    retry.click()
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)
    assert attempts >= 2
