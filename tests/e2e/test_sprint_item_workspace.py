"""The real orchestrator route preserves current/history ownership and live body edits."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_orchestrator_rail import rail_server  # noqa: F401


def _post(server: ServerHandle, path: str, body: JsonObject) -> JsonObject:
    response = httpx.post(server.base + path, json=body, timeout=10.0)
    assert response.status_code < 300, response.text
    value: JsonObject = response.json()
    return value


def _store_supervisor_conversation(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    conversation_id: str,
    created_at: int,
    reply: str,
) -> None:
    identity = json.dumps(
        [["PLAN_ACTOR", "sprint_item_supervisor"], ["PLAN_SPRINT_ITEM_ID", item_id]]
    )
    conn.execute(
        "INSERT INTO conversations (conversation_id,backend_key,model,workspace_folder,"
        "identity_environment_variables,access,latest_sequence,created_at) "
        "VALUES (?,'codex','gpt-5.6-sol','/tmp/workspace',?,'full',2,?)",
        (conversation_id, identity, created_at),
    )
    conn.executemany(
        "INSERT INTO conversation_events "
        "(conversation_id,sequence,kind,payload,created_at) VALUES (?,?,?,?,?)",
        (
            (
                conversation_id,
                1,
                "agent_message",
                json.dumps({"text": reply}),
                created_at + 1,
            ),
            (
                conversation_id,
                2,
                "turn_ended",
                json.dumps({"ending": "completed", "error_summary": None}),
                created_at + 2,
            ),
        ),
    )


def test_sprint_item_workspace_real_route_is_live_and_keeps_history(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    project = _post(
        server,
        "/api/projects",
        {"name": "Workspace project", "summary": ""},
    )
    item = _post(
        server,
        "/api/items",
        {
            "title": "Supervise the outcome",
            "body": "The shared brief stays editable and live.",
            "project_id": project["id"],
        },
    )
    past_id = "conv_workspace_past"
    active_id = "conv_workspace_active"
    with sqlite3.connect(server.db_path) as conn:
        _store_supervisor_conversation(
            conn,
            item_id=str(item["id"]),
            conversation_id=past_id,
            created_at=1_700_000_000,
            reply="Past supervisor marker",
        )
        _store_supervisor_conversation(
            conn,
            item_id=str(item["id"]),
            conversation_id=active_id,
            created_at=1_700_000_100,
            reply="Current supervisor marker",
        )
        conn.execute(
            "UPDATE agents SET conversation_id=? WHERE agent_key=?",
            (active_id, item["supervisor"]["agent_key"]),
        )

    page = open_page(
        context_factory(),
        server,
        f"#/workspace/item/{item['id']}",
        f'[data-sprint-item-view="{item["id"]}"]',
    )
    # The picker lives in the card's own options menu, which is in the head. No row of
    # chrome sits above the card, so opening the card is what reaches the picker.
    assert page.locator("[data-ticket-conversation-history]").count() == 0
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    options = page.get_by_role("button", name="Conversation options")
    options.click()
    history = page.get_by_label("Orchestrator conversation")
    history.select_option(past_id)
    assert page.get_by_text("Past supervisor marker", exact=True).count() == 0
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.get_by_text("Past supervisor marker", exact=True).wait_for(timeout=WAIT_MS)
    assert page.locator('[data-conversation-read-only-boundary="true"]').count() == 1
    assert page.locator("[data-conversation-new-arm]").count() == 0
    options.click()
    # One Tab from the options button reaches the picker, so the way back is on the
    # keyboard as well as under the pointer.
    options.press("Tab")
    picker = page.locator("[data-conversation-history] select")
    assert picker.evaluate("element => document.activeElement === element") is True
    picker.select_option("__current__")
    assert (
        page.locator('[data-conversation-lens-choice="full"]').get_attribute("aria-pressed")
        == "true"
    )
    page.get_by_text("Current supervisor marker", exact=True).wait_for(timeout=WAIT_MS)
    page.locator('[data-conversation-lens-choice="focus"]').click()
    assert page.get_by_text("Current supervisor marker", exact=True).count() == 0

    api.direct_patch(
        server,
        f"/api/items/{item['id']}",
        {"body": "The shared brief changed through the live route."},
    )
    page.get_by_text("The shared brief changed through the live route.", exact=True).wait_for(
        timeout=WAIT_MS
    )
