"""Goal promotion and retained URLs use the live Manager conversation.

The browser and backend together prove that a retained legacy conversation cannot
replace the composer, and that the first message creates the holder conversation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from urllib.parse import unquote

from playwright.sync_api import BrowserContext, Page, Request, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle, capture_success
from tests.support.connected_rows import _bind, _conversation
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
    legacy_conversation = "retained-goal-conversation"
    _conversation(server, legacy_conversation)
    _bind(server, "agent", f"goal_{goal['id']}", legacy_conversation)
    goal_route = f"#/workspace/goal/{goal['id']}"
    context = context_factory()
    page = open_page(context, server, goal_route, f'[data-goal-page="{goal["id"]}"]')
    expect(page.get_by_text("Previous goal conversation", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Make it a Manager", exact=True).click()
    page.wait_for_url("**/#/workspace/manager/*", timeout=WAIT_MS)
    manager_id = api.get(server, f"/api/goals/{goal['id']}")["holder_id"]
    host = page.locator(f'[data-manager-view="{manager_id}"]')
    expect(host.locator("h1")).to_contain_text(goal["title"])
    expect(page.get_by_text("Previous goal conversation", exact=True)).to_have_count(0)
    manager = api.get(server, f"/api/managers/{manager_id}/workspace")["manager"]
    assert manager["conversation_id"] is None

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

    expect(page.get_by_text("Previous goal conversation", exact=True)).to_have_count(0)
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    composer = page.locator("[data-conversation-input]")
    expect(composer).to_be_editable(timeout=WAIT_MS)
    composer.fill("Help launch this guide", timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/managers/{manager_id}/conversation/send"),
        timeout=WAIT_MS,
    ) as delivered:
        composer.press("Enter", timeout=WAIT_MS)
    assert delivered.value.ok, delivered.value.text()
    conversation_id = delivered.value.json()["conversation_id"]
    assert conversation_id != legacy_conversation
    manager = api.get(server, f"/api/managers/{manager_id}/workspace")["manager"]
    assert manager["conversation_id"] == conversation_id
    wire = api.get(server, f"/api/test/signals/{conversation_id}/delivered")["texts"][-1]
    assert "Help launch this guide" in wire
    assert "You are the agent for goal " not in wire
    api.direct_post(server, f"/api/test/signals/{conversation_id}/finish", {})
    capture_success(page, "goal-holder-first-message.png")

    # Both URLs reopen the same live composer without changing the retained rows.
    page.goto(f"{server.base}/#/workspace/manager/{manager_id}")
    expect(host).to_be_visible(timeout=WAIT_MS)
    expect(page.get_by_text("Previous goal conversation", exact=True)).to_have_count(0)
    expect(page.locator("[data-conversation-input]")).to_be_editable(timeout=WAIT_MS)
    capture_success(page, "manager-holder-conversation.png")
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key=?", (f"goal_{goal['id']}",)
        ).fetchone() == (legacy_conversation,)
        assert conn.execute(
            "SELECT 1 FROM conversations WHERE conversation_id=?", (legacy_conversation,)
        ).fetchone() == (1,)


def test_leaving_a_goal_manager_with_text_blocks_opens_each_next_page(
    signal_server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Unmounting the goal's inline text editors must not abort the next page's update."""
    expect.set_options(timeout=WAIT_MS)
    server = signal_server
    goal = api.direct_post(
        server,
        "/api/goals",
        {
            "title": "Write the handbook",
            "project_id": "project_other",
            "kind": "binary",
            "primary_checklist_block_id": None,
            "page": {
                "kind": "headroom.page",
                "version": 1,
                "title": "Approach",
                "summary": "",
                "blocks": [{"type": "text", "id": "plan", "text": "Draft every chapter."}],
            },
        },
    )
    goal_manager_id = api.direct_post(server, f"/api/goals/{goal['id']}/make-manager", {})[
        "manager_id"
    ]
    other = api.direct_post(
        server, "/api/managers", {"title": "Other manager", "project_id": "project_other"}
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Handbook index",
            "worker_type": "coding",
            "brief": "Agreed brief.",
            "manager": {"kind": "manager", "id": other["id"]},
        },
    )
    goal_manager_route = f"#/workspace/manager/{goal_manager_id}"
    goal_manager = f'[data-manager-view="{goal_manager_id}"]'
    page = open_page(context_factory(), server, goal_manager_route, goal_manager)
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    # The goal's text block is mounted as an inline editor: the teardown under test.
    text_editor = page.locator(goal_manager).get_by_role("textbox", name="Text", exact=True)

    def navigate(route: str) -> None:
        # In-app hash navigation: the old page is torn down by the client, not a reload.
        page.evaluate("route => { window.location.hash = route }", route)

    destinations = [
        (
            "#/workspace/chief-of-staff",
            "/api/chief/page",
            page.locator(".chief-workspace-host h1"),
            "Chief of Staff",
        ),
        (
            f"#/workspace/manager/{other['id']}",
            f"/api/managers/{other['id']}/",
            page.locator(f'[data-manager-view="{other["id"]}"] h1'),
            other["title"],
        ),
        (
            f"#/workspace/{ticket['id']}",
            f"/api/tickets/{ticket['id']}/",
            page.locator(f'[data-ticket-id="{ticket["id"]}"]').get_by_text(ticket["title"]).first,
            ticket["title"],
        ),
    ]
    for route, request_path, content, title in destinations:
        navigate(goal_manager_route)
        expect(text_editor).to_contain_text("Draft every chapter.")

        # Some reads travel as resource-body?key=<path>, so match the decoded URL.
        def own_read(request: Request, path: str = request_path) -> bool:
            return path in unquote(request.url)

        with page.expect_request(own_read, timeout=WAIT_MS):
            navigate(route)
        expect(content).to_contain_text(title)
    assert page_errors == []
