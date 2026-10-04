"""Real stored failed turns must not misclassify completed work in the shared rail.

Material risk: backend attention reaches the live board's Ticket groups and Item
rollups while the same stored failure stays visible in the Ticket conversation.
Mocked frontend rows cannot prove the API projection and retained history are wired
through the real routes. This isolated server never starts a Worker turn.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle, open_status_group

from planner.conversation.events import ConversationTurnEnding, TurnEndedEventPayload
from planner.conversation.storage import ConversationStore
from planner.core.db import connect
from planner.tickets.data import write_ticket_conversation_start


def _failed_history(server: ServerHandle, ticket_id: str) -> None:
    conversation_id = f"historical-error-{ticket_id}"
    response = httpx.post(
        server.base + "/api/conversation/conversations",
        json={"conversation_id": conversation_id, "backend_key": "codex", "model": "e2e-model"},
        timeout=10,
    )
    assert response.status_code == 201, response.text
    with connect(str(server.db_path)) as conn:
        write_ticket_conversation_start(
            conn,
            ticket_id,
            conversation_id=conversation_id,
            backend="codex",
            model="e2e-model",
            reasoning_effort=None,
            now=1,
        )
    failures: list[BaseException] = []

    def write() -> None:
        try:
            asyncio.run(
                ConversationStore(str(server.db_path)).append_event(
                    conversation_id,
                    TurnEndedEventPayload(
                        ending=ConversationTurnEnding.failed,
                        error_summary="Historical Worker failure retained",
                    ),
                )
            )
        except BaseException as error:  # noqa: BLE001 - propagate thread failures
            failures.append(error)

    writer = threading.Thread(target=write)
    writer.start()
    writer.join(timeout=10)
    assert not writer.is_alive()
    if failures:
        raise failures[0]


def _complete(server: ServerHandle, api: ApiHelper, ticket: JsonObject) -> None:
    ticket_id = ticket["id"]
    api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/accept/brief",
        {
            "next_ceiling": "none",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    for field in ("success_condition", "what_changes", "plan", "implementation", "consequences"):
        response = httpx.post(
            server.base + f"/api/tickets/{ticket_id}/propose",
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
            json={"body": f"Agreed {field}."},
            timeout=10,
        )
        assert response.status_code == 200, response.text
        done = api.direct_post(
            server,
            f"/api/tickets/{ticket_id}/accept/{field}",
            {
                "next_ceiling": "none",
                "next_holder": {"kind": "owner", "id": "owner"},
            },
        )
    assert (done["stage"], done["ticket_status"]) == ("done", "empty")


def test_completed_failed_history_api_rail_rollup_and_visible_record(
    server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    project = api.direct_post(
        server,
        "/api/projects",
        {
            "name": "Historical errors proof",
            "summary": "",
            "priority": "P2",
        },
    )
    items = [
        api.direct_post(
            server,
            "/api/items",
            {
                "title": title,
                "body": "",
                "project_id": project["id"],
                "priority": "P2",
            },
        )
        for title in ("Completed work only", "Mixed work")
    ]
    tickets = [
        api.direct_post(
            server,
            "/api/tickets",
            {
                "title": title,
                "worker_type": "coding",
                "kickoff_note": "Agreed brief.",
                "sprint_item_id": items[index]["id"],
            },
        )
        for title, index in (
            ("Completed with history", 0),
            ("Also completed", 1),
            ("Unfinished failure", 1),
        )
    ]
    for ticket in tickets[:2]:
        _complete(server, api, ticket)
    api.direct_post(
        server,
        f"/api/tickets/{tickets[2]['id']}/accept/brief",
        {
            "next_ceiling": "none",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    for ticket in tickets:
        _failed_history(server, ticket["id"])
    board = api.get(server, "/api/board")
    cards = {card["id"]: card for column in board["columns"] for card in column["cards"]}
    summaries = {item["id"]: item for item in board["sprint_items"]}
    context = context_factory()
    page = open_page(context, server, "#/workspace", '[data-screen="workspace"]')
    evidence = Path(__file__).resolve().parents[2] / "data/ticket-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    page.get_by_role("tab", name="Tickets", exact=True).click(timeout=WAIT_MS)
    page.screenshot(path=str(evidence / "completed-errors-tickets.png"), full_page=True)
    page.get_by_role("tab", name="Sprint Items", exact=True).click(timeout=WAIT_MS)
    expect(page.locator(f'[data-sprint-item="{items[0]["id"]}"]')).to_be_visible(timeout=WAIT_MS)
    page.screenshot(path=str(evidence / "completed-errors-workspace.png"), full_page=True)
    (evidence / "completed-errors-api.json").write_text(json.dumps(board, indent=2))
    assert [cards[ticket["id"]]["agent_state"] for ticket in tickets] == ["idle", "idle", "errored"]
    assert summaries[items[0]["id"]]["ticket_rollup"]["agent_state"] == "idle"
    assert summaries[items[1]["id"]]["ticket_rollup"]["agent_state"] == "errored"
    for item in items:
        rail_item = page.locator(f'[data-sprint-item="{item["id"]}"]')
        expect(rail_item).to_be_visible(timeout=WAIT_MS)
    completed_item = page.locator(f'[data-sprint-item="{items[0]["id"]}"]')
    assert completed_item.locator('[data-bucket-key="errored"]').count() == 0
    mixed_item = page.locator(f'[data-sprint-item="{items[1]["id"]}"]')
    expect(
        mixed_item.locator(f'[data-bucket-key="errored"] [data-ticket-id="{tickets[2]["id"]}"]')
    ).to_be_visible(timeout=WAIT_MS)
    assert (
        mixed_item.locator(
            f'[data-bucket-key="errored"] [data-ticket-id="{tickets[1]["id"]}"]'
        ).count()
        == 0
    )
    page.get_by_role("tab", name="Tickets", exact=True).click(timeout=WAIT_MS)
    open_status_group(page, "done")
    for ticket in tickets[:2]:
        row = page.locator(f'[data-bucket-key="done"] [data-ticket-id="{ticket["id"]}"]')
        expect(row).to_be_visible(timeout=WAIT_MS)
        expect(row.locator('[data-agent-state="idle"]')).to_have_count(1, timeout=WAIT_MS)
    page.screenshot(path=str(evidence / "completed-errors-tickets.png"), full_page=True)
    ticket_page = open_page(
        context, server, f"#/workspace/{tickets[0]['id']}", '[data-screen="ticket"]'
    )
    ticket_page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    ticket_page.locator('[data-conversation-lens-choice="full"]').click(timeout=WAIT_MS)
    failure = ticket_page.locator('[data-conversation-row="turn_ended"]')
    expect(failure).to_contain_text("Historical Worker failure retained", timeout=WAIT_MS)
    ticket_page.screenshot(
        path=str(evidence / "completed-error-history-retained.png"), full_page=True
    )
