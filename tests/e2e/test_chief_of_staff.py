"""What the Chief's panel opens on before there is a conversation.

The rest of this file's browser coverage was retired when the verification tiers were
right-sized; what is kept is the one thing that had no proof before — a panel with no
conversation showing what a first message would actually run on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

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
        "#/workspace/chief-of-staff",
        'section[data-screen="workspace"] [data-conversation-input]',
    )

    # The rail is drawn down the side of the model picker's panel, which is where a
    # person goes to see or change what the next message would start.
    page.click(f"{MODEL_PICKER} [data-conversation-picker-trigger]")
    page.wait_for_selector("[data-conversation-backend-showing]", timeout=WAIT_MS)
    assert page.inner_text("[data-conversation-backend-showing]") == "Claude"
    # And the model beside it is the Chief's own, whether or not this machine has claude
    # installed to name it more prettily than the value itself.
    assert "sonnet" in page.inner_text(
        f"{MODEL_PICKER} [data-conversation-picker-trigger]"
    ).lower()


def test_chief_workspace_route_keeps_conversation_and_navigation_active(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    page.set_viewport_size({"width": 1200, "height": 800})
    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('[data-screen="workspace"] [data-chief-destination]', timeout=WAIT_MS)

    row = page.locator('[data-chief-destination]')
    conversation = page.locator(".board-workspace-right--chief")
    assert row.is_visible()
    assert not conversation.is_visible()
    assert page.locator("[data-chief-conversation-loading]").count() == 0

    row.click()
    page.wait_for_url("**/#/workspace/chief-of-staff", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)
    assert conversation.is_visible()
    assert row.get_attribute("aria-current") == "page"
    assert page.locator('.shell-links [data-screen="home"]').is_visible()

    page.reload()
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)
    assert page.locator(".board-workspace-right--chief").is_visible()

    page.set_viewport_size({"width": 390, "height": 700})
    assert page.locator(".board-workspace-left").is_hidden()
    assert page.locator(".board-workspace-right--chief").is_visible()


def test_mobile_workspace_keeps_chief_conversation_unmounted_until_selected(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    page.set_viewport_size({"width": 390, "height": 700})
    chief_lookups: list[str] = []
    page.on(
        "request",
        lambda request: chief_lookups.append(request.url)
        if request.url.endswith("/api/chief/conversation")
        else None,
    )

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('[data-screen="workspace"] [data-chief-destination]', timeout=WAIT_MS)
    assert page.locator("[data-conversation-input]").count() == 0
    assert page.locator('[data-chief-destination]').get_attribute(
        "aria-current"
    ) is None
    assert chief_lookups == []

    page.locator('[data-chief-destination]').click()
    page.wait_for_url("**/#/workspace/chief-of-staff", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)
    assert len(chief_lookups) == 1


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
    page.goto(server.base + "/#/workspace/chief-of-staff")
    page.wait_for_selector("[data-chief-conversation-error]", timeout=WAIT_MS)
    assert "HTTP 503" in page.locator("[data-chief-conversation-error]").inner_text()
    retry = page.locator("[data-chief-conversation-retry]")
    assert retry.is_visible()
    allow_success = True
    retry.click()
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)
    assert attempts >= 2


def test_delayed_chief_lookup_shows_loading_before_the_conversation(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    pending: list[Route] = []

    def hold_lookup(route: Route) -> None:
        if route.request.url.endswith("/api/chief/conversation"):
            pending.append(route)
        else:
            route.continue_()

    page.route("**/api/chief/conversation*", hold_lookup)
    page.goto(server.base + "/#/workspace/chief-of-staff")
    page.wait_for_selector("[data-chief-conversation-loading]", timeout=WAIT_MS)
    assert page.locator("[data-conversation-input]").count() == 0
    assert len(pending) == 1

    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body='{"conversation_id":null}',
    )
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)


def test_chief_canonical_id_send_and_reset_survive_desktop_navigation(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    sent_bodies: list[dict[str, object]] = []
    canonical_state: dict[str, str | None] = {"conversation_id": None}
    opened_ids: list[str] = []
    reset_states: list[str | None] = []

    def canonical_api(route: Route) -> None:
        url = route.request.url
        if url.endswith("/api/chief/conversation"):
            route.fulfill(
                status=200,
                content_type="application/json",
                json=canonical_state,
            )
            return
        if url.endswith("/api/chief/conversation/send"):
            body = route.request.post_data_json
            assert isinstance(body, dict)
            sent_bodies.append(cast(dict[str, object], body))
            next_id = f"canonical-chief-{len(sent_bodies)}"
            canonical_state["conversation_id"] = next_id
            route.fulfill(
                status=200,
                content_type="application/json",
                json={"conversation_id": next_id, "fate": "started"},
            )
            return
        if url.endswith("/api/chief/conversation/reset"):
            canonical_state["conversation_id"] = None
            reset_states.append(canonical_state["conversation_id"])
            route.fulfill(
                status=200,
                content_type="application/json",
                json=canonical_state,
            )
            return
        route.continue_()

    def canonical_conversation(route: Route) -> None:
        url = route.request.url
        conversation_id = next(
            identifier
            for identifier in ("canonical-chief-1", "canonical-chief-2")
            if f"/conversations/{identifier}" in url
        )
        if "/events?" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                json={"events": []},
            )
            return
        if "/tail?" in url:
            route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=": complete\n\n",
            )
            return
        opened_ids.append(conversation_id)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                f'{{"conversation_id":"{conversation_id}","backend_key":"codex",'
                '"model":"gpt-5.6-sol","reasoning_effort":"medium",'
                '"workspace_folder":"/workspace","access":"direct","role_text":null,'
                '"identity_environment_variable_names":[],"latest_sequence":0,'
                '"is_running":false,"held_prompts":[],'
                '"pending_permission_ask":null,"pending_user_input":null,'
                '"available_commands":[]}'
            ),
        )

    page.route("**/api/chief/conversation", canonical_api)
    page.route("**/api/chief/conversation/send", canonical_api)
    page.route("**/api/chief/conversation/reset", canonical_api)
    page.route(
        "**/api/conversation/conversations/canonical-chief-**",
        canonical_conversation,
    )
    page.set_viewport_size({"width": 1200, "height": 800})
    page.goto(server.base + "/#/workspace")
    page.wait_for_selector("[data-chief-destination]", timeout=WAIT_MS)
    page.locator("[data-chief-destination]").click()
    page.wait_for_url("**/#/workspace/chief-of-staff", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)
    page.locator("[data-conversation-input]").fill("use the canonical thread")
    with page.expect_response(
        lambda response: response.url.endswith(
            "/api/conversation/conversations/canonical-chief-1"
        ),
        timeout=WAIT_MS,
    ):
        with page.expect_response(
            lambda response: response.url.endswith("/api/chief/conversation/send"),
            timeout=WAIT_MS,
        ):
            page.locator("[data-conversation-send]").click()
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-input]')?.value === ''"
    )
    assert len(sent_bodies) == 1
    assert sent_bodies[0]["conversation_id"] is None
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-input]')?.placeholder"
        " === 'Message Chief of Staff...'"
    )
    assert canonical_state["conversation_id"] == "canonical-chief-1"
    assert "canonical-chief-1" in opened_ids

    page.set_viewport_size({"width": 390, "height": 700})
    assert page.locator("[data-conversation-input]").is_visible()
    page.set_viewport_size({"width": 1200, "height": 800})
    page.goto(server.base + "/#/workspace/chief-of-staff")
    page.wait_for_selector("[data-conversation-input]", timeout=WAIT_MS)

    page.locator('[aria-label="Conversation options"]').click()
    page.locator("[data-conversation-new-arm]").click()
    with page.expect_response(
        lambda response: response.url.endswith("/api/chief/conversation/reset"),
        timeout=WAIT_MS,
    ):
        page.locator("[data-conversation-new-confirm]").click()
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-input]')?.placeholder"
        " === 'Send the first message to start it...'"
    )
    assert reset_states == [None]
    assert canonical_state["conversation_id"] is None

    page.locator("[data-conversation-input]").fill("start a genuinely new thread")
    with page.expect_response(
        lambda response: response.url.endswith(
            "/api/conversation/conversations/canonical-chief-2"
        ),
        timeout=WAIT_MS,
    ):
        with page.expect_response(
            lambda response: response.url.endswith("/api/chief/conversation/send"),
            timeout=WAIT_MS,
        ):
            page.locator("[data-conversation-send]").click()
    assert len(sent_bodies) == 2
    assert sent_bodies[1]["conversation_id"] is None
    assert canonical_state["conversation_id"] == "canonical-chief-2"
    assert "canonical-chief-2" in opened_ids
