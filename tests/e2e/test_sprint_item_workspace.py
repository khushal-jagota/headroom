"""The real Sprint Item route composes its workspace, transcript, and live updates."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


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


def test_sprint_item_workspace_real_route_is_responsive_live_and_keeps_history(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    project = _post(
        server,
        "/api/projects",
        {"name": "Workspace project", "summary": "", "priority": "P2"},
    )
    sprint = _post(
        server,
        "/api/sprints",
        {"name": "Workspace sprint", "date_start": "2026-07-01", "date_end": "2026-07-07"},
    )
    item = _post(
        server,
        "/api/items",
        {
            "title": "Supervise the outcome",
            "body": "The shared brief stays editable and live.",
            "priority": "P2",
            "project_id": project["id"],
            "sprint_id": sprint["id"],
        },
    )
    today_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Work today",
            "worker_type": "coding",
            "kickoff_note": "Start.",
            "sprint_item_id": item["id"],
        },
    )
    review_ticket = _post(
        server,
        "/api/tickets",
        {
            "title": "Review off today",
            "worker_type": "coding",
            "kickoff_note": "Start.",
            "sprint_item_id": item["id"],
        },
    )
    removed = httpx.delete(
        f"{server.base}/api/day/today/tickets/{review_ticket['id']}", timeout=10.0
    )
    assert removed.status_code < 300, removed.text
    api.direct_post(
        server,
        f"/api/tickets/{review_ticket['id']}/accept/kickoff",
        {"next_ceiling": "needs_success", "at_cap": "agent_review"},
    )
    proposed = httpx.post(
        f"{server.base}/api/tickets/{review_ticket['id']}/propose/success",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(review_ticket["id"])},
        json={"body": "The outcome is proven."},
        timeout=10.0,
    )
    assert proposed.status_code < 300, proposed.text
    artifact = httpx.put(
        f"{server.base}/api/items/{item['id']}/supervisor/artifacts/proof.md",
        headers={
            "X-Plan-Actor": "sprint_item_supervisor",
            "X-Plan-Sprint-Item-ID": str(item["id"]),
        },
        json={"content": "# Proof"},
        timeout=10.0,
    )
    assert artifact.status_code < 300, artifact.text

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
        f"#/sprint?item={item['id']}",
        f'[data-sprint-item-view="{item["id"]}"]',
    )
    page.locator(f'[data-sprint-ticket-id="{today_ticket["id"]}"]').wait_for(
        timeout=WAIT_MS
    )
    # Remaining Tickets starts collapsed, and its rows carry the shared row grammar:
    # the condition is the stage mark's label, not a separate word.
    remaining = page.locator('[data-workspace-section="remaining"]')
    assert remaining.get_attribute("open") is None
    remaining.locator("summary").click()
    off_today = page.locator(f'[data-sprint-ticket-id="{review_ticket["id"]}"]')
    assert off_today.get_attribute("data-ticket-state") == "current-awaiting-approval"
    off_today.get_by_label("to review").wait_for(timeout=WAIT_MS)
    page.get_by_text("proof.md", exact=True).wait_for(timeout=WAIT_MS)

    history = page.get_by_label("Sprint Item conversation")
    history.select_option(past_id)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.get_by_text("Past supervisor marker", exact=True).wait_for(timeout=WAIT_MS)
    assert page.locator('[data-conversation-read-only-boundary="true"]').count() == 1
    history.select_option("__current__")
    page.get_by_text("Current supervisor marker", exact=True).wait_for(timeout=WAIT_MS)

    api.direct_patch(
        server,
        f"/api/items/{item['id']}",
        {"body": "The shared brief changed through the live route."},
    )
    page.get_by_text("The shared brief changed through the live route.", exact=True).wait_for(
        timeout=WAIT_MS
    )

    page.set_viewport_size({"width": 390, "height": 844})
    page.locator('[data-sprint-item-view]').wait_for(timeout=WAIT_MS)
    assert page.locator("[data-conversation-input]").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= 390") is True
