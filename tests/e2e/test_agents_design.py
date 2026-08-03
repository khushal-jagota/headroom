from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Route
from tests.e2e.harness import ServerHandle

WAIT_MS = 10_000


def test_chief_of_staff_lives_above_workspace_projects(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()

    page.route(
        "**/api/workers",
        lambda route: route.fulfill(
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
                    "conversation_id": "conv-chief-workspace",
                    "needs_me": False,
                    "agent_working": False,
                    "latest_turn_ended_sequence": 0,
                },
            },
        ),
    )
    page.route(
        "**/api/chief/conversation",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={"conversation_id": "conv-chief-workspace"},
        ),
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
                "conversation_id": "conv-chief-workspace",
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

    page.route(
        "**/api/conversation/conversations/conv-chief-workspace**",
        conversation_response,
    )
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(server.base + "/#/workspace")

    row = page.locator('[data-chief-destination]')
    row.wait_for(state="visible", timeout=WAIT_MS)
    assert row.locator(".board-workspace-chief-name").inner_text() == "Chief of Staff"
    assert row.evaluate(
        "node => node.nextElementSibling?.classList.contains('board-workspace-project-filter')"
    )
    assert page.locator('[data-project-filter]').inner_text().startswith("All projects")
    assert row.locator('[data-stage-state="upcoming"]').count() == 1

    row.click()
    page.wait_for_url("**/#/workspace/chief-of-staff", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-pane]", timeout=WAIT_MS)
    assert row.get_attribute("aria-current") == "page"
    assert page.locator(".board-workspace-right--chief").is_visible()
