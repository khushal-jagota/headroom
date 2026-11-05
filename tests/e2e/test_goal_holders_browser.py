"""Promotion and the stable Goal URL use the same Manager name."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.support.server_fixtures import signal_server as signal_server


def test_goal_promotion_and_stable_url_share_manager_name(
    signal_server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    expect.set_options(timeout=WAIT_MS)
    server = signal_server
    goal = api.direct_post(
        server,
        "/api/goals",
        {
            "title": "Launch the guide",
            "project_id": "project_other",
            "kind": "binary",
            "primary_checklist_block_id": None,
        },
    )
    goal_route = f"#/workspace/goal/{goal['id']}"
    context = context_factory()
    page = open_page(context, server, goal_route, f'[data-goal-page="{goal["id"]}"]')
    page.get_by_role("button", name="Make it a Manager", exact=True).click()
    page.wait_for_url("**/#/workspace/manager/*", timeout=WAIT_MS)
    manager_id = api.get(server, f"/api/goals/{goal['id']}")["holder_id"]
    host = page.locator(f'[data-manager-view="{manager_id}"]')
    expect(host.locator("h1")).to_contain_text(goal["title"])

    # The retained Goal URL edits the Manager name while its stored goal title stays hidden.
    page.goto(f"{server.base}/{goal_route}")
    name = "Launch team"
    name_editor = host.get_by_role("textbox", name="Manager name", exact=True)
    name_editor.fill(name)
    name_editor.press("Tab")
    rail_title = page.locator(f'.manager-card[data-manager="{manager_id}"] .item-title')
    # The rail reflects the accepted save before refreshing the page.
    expect(rail_title).to_have_text(name)
    page.reload()
    expect(name_editor).to_have_text(name)
    expect(rail_title).to_have_text(name)
