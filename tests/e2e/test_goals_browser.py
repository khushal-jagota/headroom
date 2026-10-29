"""Saved goal edits and conversations cross the browser, API, store and change stream.

The shared scripted-backend server proves the actual first-message ownership path without
starting a provider. Unit tests cannot prove mounted queries receive external goal edits.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.support.server_fixtures import signal_server as signal_server


def test_goal_page_persistence_external_refresh_and_owned_conversation(
    signal_server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    server = signal_server
    project = api.direct_post(server, "/api/projects", {"name": "Goal browser proof"})
    source = api.direct_post(
        server,
        "/api/measure-sources",
        {
            "name": "Collected reading",
            "project_id": project["id"],
            "account": "browser-proof",
            "responsible": {"kind": "chief", "id": "chief"},
        },
    )
    measure = api.direct_post(
        server,
        "/api/measures",
        {
            "project_id": project["id"],
            "name": "Draft words",
            "unit": "words",
            "origin": "taken",
            "kind": "reading",
            "timezone": "UTC",
            "source_id": source["id"],
        },
    )

    def collect(value: int) -> str:
        current_source = api.get(server, f"/api/measure-sources/{source['id']}")
        configured = api.direct_put(
            server,
            f"/api/measure-sources/{source['id']}/script",
            {
                "expected_revision": current_source["source_revision"],
                "text": "output=context['required_outputs'][0]\n"
                f"record(output['measure_id'], output['required_date'], {value})",
            },
        )
        run = api.direct_post(
            server,
            f"/api/measure-sources/{source['id']}/runs",
            {
                "mode": "collection",
                "expected_script_revision": configured["active_script"]["revision"],
            },
        )
        return str(run["id"])

    first_run = collect(42)
    goal = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Publish a field guide",
            "kind": "binary",
            "page": {
                "kind": "headroom.page",
                "version": 1,
                "title": "Approach",
                "summary": "",
                "blocks": [
                    {
                        "type": "checklist",
                        "id": "approach",
                        "items": [
                            {"id": "outline", "label": "Write the outline", "state": "open"},
                            {"id": "review", "label": "Review the draft", "state": "open"},
                        ],
                    },
                    {
                        "type": "table",
                        "id": "notes",
                        "columns": ["Task", "Note"],
                        "rows": [["Outline", "First"], ["Review", "Second"]],
                    },
                    {
                        "type": "chart",
                        "id": "reading",
                        "kind": "number",
                        "placement": "supporting",
                        "title": "Draft words",
                        "measure_id": measure["id"],
                    },
                ],
            },
        },
    )
    path = f"/api/goals/{goal['id']}"
    page = open_page(
        context_factory(), server, f"#/workspace/goal/{goal['id']}", "[data-goal-page]"
    )
    expect(page.get_by_role("heading", name="Publish a field guide")).to_be_visible(timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as saved:
        page.get_by_role("checkbox", name="Write the outline", exact=True).click(timeout=WAIT_MS)
    assert saved.value.ok
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"
    page.reload()
    page.wait_for_selector("[data-goal-page]", timeout=WAIT_MS)
    # Completed steps move into the saved disclosure instead of disappearing on reload.
    page.locator("[data-group-toggle]").click(timeout=WAIT_MS)
    expect(page.get_by_role("checkbox", name="Write the outline", exact=True)).to_be_checked(
        timeout=WAIT_MS
    )
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ):
        page.get_by_role("button", name="Mark done", exact=True).click(timeout=WAIT_MS)
    current = api.get(server, path)
    assert current["completed"] is True and current["active"] is True
    assert any(
        row["id"] == goal["id"] for row in api.get(server, "/api/goals?active=true")["goals"]
    )
    # The approved page keeps the active state with the other goal fields, behind Edit goal.
    page.get_by_role("button", name="Edit goal", exact=True).click(timeout=WAIT_MS)
    page.get_by_label("State", exact=True).select_option(label="Inactive")
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ):
        page.get_by_role("button", name="Save", exact=True).click(timeout=WAIT_MS)
    expect(page.locator("[data-goal-page] .goal-state")).to_have_text("Inactive", timeout=WAIT_MS)
    current = api.get(server, path)
    assert current["completed"] is True and current["active"] is False
    updated_page = current["page"]
    updated_page["blocks"][0]["items"][1]["label"] = "Review the updated draft"
    response = httpx.patch(
        server.base + path,
        headers={"X-Plan-Actor": "goal", "X-Plan-Goal-ID": goal["id"]},
        json={
            "expected_revision": current["revision"],
            "page": updated_page,
            "completed": False,
            "active": True,
        },
        timeout=10,
    )
    assert response.is_success, response.text
    expect(page.get_by_role("button", name="Mark done", exact=True)).to_be_visible(timeout=WAIT_MS)
    expect(page.locator("[data-goal-page] .goal-state")).to_have_text("Active", timeout=WAIT_MS)
    expect(page.get_by_role("checkbox", name="Review the updated draft", exact=True)).to_be_visible(
        timeout=WAIT_MS
    )
    reading = page.locator('[data-chart-id="reading"] [data-current]')
    expect(reading).to_have_attribute("data-current", "42", timeout=WAIT_MS)
    assert api.get(server, f"/api/measure-source-runs/{first_run}")["run"]["status"] == "succeeded"
    collect(52)
    expect(reading).to_have_attribute("data-current", "52", timeout=WAIT_MS)
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"

    # Authored text is read-only on a goal page. The goal agent still writes the document,
    # and the page shows the written words without an editor of its own.
    assert page.locator("[data-goal-page] [contenteditable]").count() == 0
    current = api.get(server, path)
    reordered = current["page"]
    reordered["blocks"][1]["rows"].reverse()
    with page.expect_response(
        lambda reply: urlparse(reply.url).path == "/api/resource-body"
        and parse_qs(urlparse(reply.url).query).get("key") == [path],
        timeout=WAIT_MS,
    ):
        remote = httpx.patch(
            server.base + path,
            headers={"X-Plan-Actor": "goal", "X-Plan-Goal-ID": goal["id"]},
            json={
                "expected_revision": current["revision"],
                "title": "Agent title",
                "page": reordered,
            },
            timeout=10,
        )
        assert remote.is_success, remote.text
    expect(page.locator("[data-goal-page] h1")).to_have_text("Agent title", timeout=WAIT_MS)
    expect(page.locator('[data-block="table"] tbody tr').first).to_contain_text(
        "Second", timeout=WAIT_MS
    )
    assert api.get(server, path)["page"]["blocks"][1]["rows"] == [
        ["Review", "Second"], ["Outline", "First"]
    ]

    # The rail must subscribe to another active goal, even while this one is selected.
    other = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Another active goal",
            "kind": "binary",
        },
    )
    other_message = api.direct_post(
        server,
        "/api/messages/send",
        {
            "target": {"kind": "goal", "id": other["id"]},
            "message": "Work on the other goal",
        },
    )
    other_id = other_message["conversation_id"]
    rail_goal = page.locator(f'[data-goal-id="{other["id"]}"]')
    expect(rail_goal.locator('[data-conversation-signal="working"]')).to_have_count(
        1, timeout=WAIT_MS
    )
    api.direct_post(
        server,
        f"/api/test/connected/{other_id}/message",
        {
            "kind": "goal",
            "id": other["id"],
            "text": "A question from the other goal",
        },
    )
    expect(rail_goal.locator('[data-conversation-signal="message"]')).to_have_count(
        1, timeout=WAIT_MS
    )
    api.direct_post(server, f"/api/test/signals/{other_id}/finish", {})
    # Opening the page did not start a model. Its first browser message must do so.
    assert api.get(server, path)["conversation_id"] is None
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    type_instead = page.get_by_role("button", name="Type instead", exact=True)
    if type_instead.is_visible():
        type_instead.click(timeout=WAIT_MS)
    page.locator("[data-conversation-input]").fill("Help with this goal", timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path + "/conversation/send"), timeout=WAIT_MS
    ) as delivered:
        page.locator("[data-conversation-input]").press("Enter", timeout=WAIT_MS)
    assert delivered.value.ok, delivered.value.text()
    conversation_id = delivered.value.json()["conversation_id"]
    wire = api.get(server, f"/api/test/signals/{conversation_id}/delivered")["texts"][-1]
    assert "You are the agent for goal " + goal["id"] in wire
    assert "Help with this goal" in wire
    api.direct_post(server, f"/api/test/signals/{conversation_id}/finish", {})
    page.get_by_role("button", name="Conversation options", exact=True).click(timeout=WAIT_MS)
    page.locator("[data-conversation-new-arm]").click(timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path + "/conversation/reset"), timeout=WAIT_MS
    ) as reset:
        page.locator("[data-conversation-new-confirm]").click(timeout=WAIT_MS)
    assert reset.value.ok
    history = api.get(server, f"/api/conversation-history/goal/{goal['id']}")
    assert [entry["conversation_id"] for entry in history["entries"]] == [conversation_id]
    assert api.get(server, path)["conversation_id"] is None
    page.reload()
    page.wait_for_selector("[data-goal-page]", timeout=WAIT_MS)
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"


def test_goal_files_open_from_catalog_and_historical_conversation(
    signal_server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Connect goal catalog and Markdown links to served bytes in the workspace frame.

    The existing server and browser fixtures prove this connection without another host.
    Pure address tests cannot prove the mounted goal page intercepts both link sources.
    """
    server = signal_server
    project = api.direct_post(server, "/api/projects", {"name": "Goal file browser proof"})
    goal = api.direct_post(
        server,
        "/api/goals",
        {"project_id": project["id"], "title": "Keep the field guide", "kind": "binary"},
    )
    goal_path = f"/api/goals/{goal['id']}"
    file_path = f"/files/goals/{goal['id']}/artifacts/field%20guide.md"
    api.direct_put(
        server, file_path, {"content": "# Durable field guide\n\nSaved supporting material."}
    )
    page = open_page(
        context_factory(), server, f"#/workspace/goal/{goal['id']}", "[data-goal-page]"
    )
    page.locator("[data-artifacts-band] summary").click(timeout=WAIT_MS)
    page.locator('[data-artifact-folder="artifacts"]').click(timeout=WAIT_MS)
    page.locator('[data-artifact-chip="artifacts/field guide.md"]').click(timeout=WAIT_MS)
    artifact = page.locator("[data-ticket-artifact]")
    expect(artifact.get_by_role("heading", name="Durable field guide")).to_be_visible(
        timeout=WAIT_MS
    )
    assert f"/workspace/goal/{goal['id']}?" in page.url
    assert parse_qs(urlparse(page.url).fragment.split("?", 1)[1])["source"] == ["goal"]
    page.go_back()
    expect(artifact).to_have_count(0, timeout=WAIT_MS)
    expect(page.get_by_role("heading", name="Keep the field guide")).to_be_visible(timeout=WAIT_MS)

    sent = api.direct_post(
        server,
        "/api/messages/send",
        {"target": {"kind": "goal", "id": goal["id"]}, "message": "Keep this guide"},
    )
    conversation_id = sent["conversation_id"]
    api.direct_post(
        server,
        f"/api/test/connected/{conversation_id}/message",
        {"kind": "goal", "id": goal["id"], "text": f"[Field guide]({file_path})"},
    )
    api.direct_post(server, f"/api/test/signals/{conversation_id}/finish", {})
    api.direct_post(server, goal_path + "/conversation/reset", {})
    page.goto(server.base + f"/#/workspace/goal/{goal['id']}?conversation={conversation_id}")
    conversation_link = page.get_by_role("link", name="Open field guide.md", exact=True)
    conversation_link.filter(visible=True).first.click(timeout=WAIT_MS)
    expect(artifact.get_by_role("heading", name="Durable field guide")).to_be_visible(
        timeout=WAIT_MS
    )
    opened_url = page.url
    opened_params = parse_qs(urlparse(opened_url).fragment.split("?", 1)[1])
    assert opened_params["conversation"] == [conversation_id]
    page.reload()
    expect(artifact.get_by_role("heading", name="Durable field guide")).to_be_visible(
        timeout=WAIT_MS
    )
    assert page.url == opened_url
    page.locator("[data-ticket-artifact-close]").click(timeout=WAIT_MS)
    expect(artifact).to_have_count(0, timeout=WAIT_MS)
    closed_params = parse_qs(urlparse(page.url).fragment.split("?", 1)[1])
    assert closed_params == {"conversation": [conversation_id]}
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    expect(conversation_link.filter(visible=True).first).to_be_visible(timeout=WAIT_MS)
