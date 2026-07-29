from __future__ import annotations

from collections.abc import Callable
from typing import cast

from playwright.sync_api import BrowserContext, Route, expect
from tests.e2e.harness import ServerHandle

WAIT_MS = 10_000


def test_agents_roster_states_design_and_compact_read_watermark(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    signals: dict[str, object] = {
        "conversation_id": "conv-chief-design",
        "needs_me": False,
        "agent_working": False,
        "latest_turn_ended_sequence": 0,
    }

    def workers_response(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "workers": [],
                "chief_of_staff": {
                    "employee_id": "chief_of_staff",
                    "label": "Chief of Staff",
                    "skill": {
                        "name": "panels-chief-of-staff",
                        "description": "Plans and coordinates work.",
                        "markdown_body": "",
                    },
                    "launch_defaults": {
                        "employee_backend": "codex",
                        "employee_launch_model": None,
                        "employee_launch_reasoning_effort": None,
                    },
                    **signals,
                },
            },
        )

    def chief_reference(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"conversation_id": "conv-chief-design"},
        )

    def conversation_response(route: Route) -> None:
        if "/events?" in route.request.url:
            route.fulfill(
                status=200,
                content_type="application/json",
                json={"events": []},
            )
            return
        if "/tail?" in route.request.url:
            route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=": complete\n\n",
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "conversation_id": "conv-chief-design",
                "backend_key": "codex",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
                "workspace_folder": "/workspace",
                "access": "direct",
                "role_text": None,
                "identity_environment_variable_names": [],
                "latest_sequence": 9,
                "is_running": False,
                "held_prompt_count": 0,
                "pending_permission_ask": None,
                "pending_user_input": None,
                "available_commands": [],
            },
        )

    page.route("**/api/workers", workers_response)
    page.route("**/api/chief/conversation", chief_reference)
    page.route(
        "**/api/conversation/conversations/conv-chief-design**",
        conversation_response,
    )
    page.set_viewport_size({"width": 390, "height": 700})
    page.goto(server.base + "/#/agents")

    row = page.locator('[data-agent-id="chief-of-staff"]')
    mark = row.locator(".stage-mark")
    page.wait_for_selector('[data-stage-state="upcoming"]', timeout=WAIT_MS)

    def resolved_background(token: str) -> str:
        return cast(
            str,
            page.evaluate(
                """token => {
                    const probe = document.createElement("span");
                    probe.style.background = `var(${token})`;
                    document.body.append(probe);
                    const color = getComputedStyle(probe).backgroundColor;
                    probe.remove();
                    return color;
                }""",
                token,
            ),
        )

    assert page.locator(".agents-workspace-roster-head").count() == 0
    assert page.locator(".agents-roster-description").count() == 0
    assert page.locator(".agents-roster-arrow").count() == 0
    resting_background = row.evaluate(
        "(node) => getComputedStyle(node).backgroundColor"
    )
    row.hover()
    expect(row).to_have_css(
        "background-color",
        resolved_background("--surface-raised"),
    )
    hover_background = row.evaluate(
        "(node) => getComputedStyle(node).backgroundColor"
    )
    assert hover_background == resolved_background("--surface-raised")
    name_style = row.locator(".agents-roster-name").evaluate(
        "(node) => { const style = getComputedStyle(node);"
        " return [style.fontFamily, style.fontSize, style.fontWeight]; }"
    )
    assert "Newsreader" in name_style[0]
    assert name_style[1:] == ["24px", "500"]
    mark_width = float(
        mark.evaluate("(node) => getComputedStyle(node).width").removesuffix("px")
    )
    assert 14.3 < mark_width < 14.5

    state_updates: list[tuple[str, dict[str, object]]] = [
        ("current-running", {"agent_working": True}),
        ("needs-me", {"needs_me": True}),
        (
            "current-awaiting-approval",
            {
                "needs_me": False,
                "agent_working": False,
                "latest_turn_ended_sequence": 9,
            },
        ),
    ]
    for expected_state, update in state_updates:
        signals.update(update)
        page.reload()
        page.wait_for_selector(
            f'[data-stage-state="{expected_state}"]',
            timeout=WAIT_MS,
        )

    row.click()
    page.wait_for_url("**/#/agents/chief-of-staff", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-pane]", timeout=WAIT_MS)
    back = page.locator(".agents-conversation-back")
    assert back.is_visible()
    assert page.locator(".agents-workspace-roster").is_hidden()
    back.click()
    page.wait_for_url("**/#/agents", timeout=WAIT_MS)
    page.wait_for_selector('[data-stage-state="reply-seen"]', timeout=WAIT_MS)

    page.set_viewport_size({"width": 1200, "height": 800})
    expect(page.locator(".agents-workspace-roster")).to_be_visible()
    expect(page.locator(".agents-workspace-conversation")).to_be_visible()
    expect(row).to_have_attribute("aria-current", "page")
    expect(row).to_have_css(
        "background-color",
        resolved_background("--surface-overlay"),
    )
    selected_background = row.evaluate(
        "(node) => getComputedStyle(node).backgroundColor"
    )
    assert selected_background == resolved_background("--surface-overlay")
    assert selected_background not in {resting_background, hover_background}
