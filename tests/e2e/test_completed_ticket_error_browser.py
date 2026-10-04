"""Real stored failed turns must not misclassify completed work in the shared rail.

Material risk: backend attention reaches the live board's Ticket groups and Item
rollups while the same stored failure stays visible in the Ticket conversation.
Mocked frontend rows cannot prove the API projection and retained history are wired
through the real routes. The three completed fixtures faithfully replicate the
owner-reported rows with generated identities, not production snapshots. This
isolated server never starts a Worker turn.
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
from planner.tickets.data import read_ticket, write_ticket_conversation_start


def _failed_history(server: ServerHandle, ticket_id: str) -> JsonObject:
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
    facts: list[JsonObject] = []

    async def append_and_check() -> None:
        store = ConversationStore(str(server.db_path))
        await store.append_event(
            conversation_id,
            TurnEndedEventPayload(
                ending=ConversationTurnEnding.failed,
                error_summary="Historical Worker failure retained",
            ),
        )
        attention = (await store.attention_facts([conversation_id]))[conversation_id]
        assert attention.last_turn_failed is True
        with connect(str(server.db_path)) as conn:
            raw = read_ticket(conn, ticket_id)
        assert raw.worker_step_claim.value == "none"
        facts.append(
            {
                "title": raw.title,
                "stage": raw.stage,
                "ticket_status": raw.ticket_status.value,
                "worker_step_claim": raw.worker_step_claim.value,
                "last_turn_failed": attention.last_turn_failed,
            }
        )

    def write() -> None:
        try:
            asyncio.run(append_and_check())
        except BaseException as error:  # noqa: BLE001 - propagate thread failures
            failures.append(error)

    writer = threading.Thread(target=write)
    writer.start()
    writer.join(timeout=10)
    assert not writer.is_alive()
    if failures:
        raise failures[0]
    return facts[0]


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
            (
                "Make Full show the complete conversation again, and make an addressed agent reply",
                0,
            ),
            ("Two ownership modes, no overrides, no remembered ceiling", 0),
            ("One way a Ticket comes into existence", 1),
            ("Unfinished failure", 1),
        )
    ]
    for ticket in tickets[:3]:
        _complete(server, api, ticket)
    api.direct_post(
        server,
        f"/api/tickets/{tickets[3]['id']}/accept/brief",
        {
            "next_ceiling": "none",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    stored_facts = [_failed_history(server, ticket["id"]) for ticket in tickets]
    assert all(
        (facts["stage"], facts["ticket_status"]) == ("done", "empty") for facts in stored_facts[:3]
    )
    board = api.get(server, "/api/board")
    cards = {card["id"]: card for column in board["columns"] for card in column["cards"]}
    summaries = {item["id"]: item for item in board["sprint_items"]}
    context = context_factory()
    page = open_page(context, server, "#/workspace", '[data-screen="workspace"]')
    evidence = Path(__file__).resolve().parents[2] / "data/ticket-evidence/consequences"
    evidence.mkdir(parents=True, exist_ok=True)
    page.get_by_role("tab", name="Tickets", exact=True).click(timeout=WAIT_MS)
    page.screenshot(path=str(evidence / "completed-errors-tickets.png"), full_page=True)
    page.get_by_role("tab", name="Sprint Items", exact=True).click(timeout=WAIT_MS)
    expect(page.locator(f'[data-sprint-item="{items[0]["id"]}"]')).to_be_visible(timeout=WAIT_MS)
    page.screenshot(path=str(evidence / "completed-errors-workspace.png"), full_page=True)
    (evidence / "completed-errors-api.json").write_text(json.dumps(board, indent=2))
    (evidence / "faithful-fixture-facts.json").write_text(json.dumps(stored_facts, indent=2))
    assert [cards[ticket["id"]]["agent_state"] for ticket in tickets] == [
        "idle",
        "idle",
        "idle",
        "errored",
    ]
    assert summaries[items[0]["id"]]["ticket_rollup"]["agent_state"] == "idle"
    assert summaries[items[1]["id"]]["ticket_rollup"]["agent_state"] == "errored"
    for item in items:
        rail_item = page.locator(f'[data-sprint-item="{item["id"]}"]')
        expect(rail_item).to_be_visible(timeout=WAIT_MS)
    completed_item = page.locator(f'[data-sprint-item="{items[0]["id"]}"]')
    assert completed_item.locator('[data-bucket-key="errored"]').count() == 0
    mixed_item = page.locator(f'[data-sprint-item="{items[1]["id"]}"]')
    expect(
        mixed_item.locator(f'[data-bucket-key="errored"] [data-ticket-id="{tickets[3]["id"]}"]')
    ).to_be_visible(timeout=WAIT_MS)
    assert (
        mixed_item.locator(
            f'[data-bucket-key="errored"] [data-ticket-id="{tickets[2]["id"]}"]'
        ).count()
        == 0
    )
    page.get_by_role("tab", name="Tickets", exact=True).click(timeout=WAIT_MS)
    open_status_group(page, "done")
    for ticket in tickets[:3]:
        row = page.locator(f'[data-bucket-key="done"] [data-ticket-id="{ticket["id"]}"]')
        expect(row).to_be_visible(timeout=WAIT_MS)
        expect(row.locator('[data-agent-state="idle"]')).to_have_count(1, timeout=WAIT_MS)
    page.screenshot(path=str(evidence / "completed-errors-tickets.png"), full_page=True)
    for index, item in enumerate(items):
        item_page = open_page(
            context,
            server,
            f"#/workspace/item/{item['id']}",
            f'[data-sprint-item-workspace="{item["id"]}"]',
        )
        pane = item_page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')
        done_group = pane.locator('[data-workspace-group="done"]')
        if done_group.get_attribute("open") is None:
            done_group.locator("summary").click(timeout=WAIT_MS)
        for ticket in tickets[:3]:
            if ticket["sprint_item_id"] != item["id"]:
                continue
            expect(done_group.locator(f'[data-sprint-ticket-id="{ticket["id"]}"]')).to_be_visible(
                timeout=WAIT_MS,
            )
            assert (
                pane.locator(
                    f'[data-workspace-group="errored"] [data-sprint-ticket-id="{ticket["id"]}"]'
                ).count()
                == 0
            )
        if index == 1:
            expect(
                pane.locator(
                    f'[data-workspace-group="errored"] [data-sprint-ticket-id="{tickets[3]["id"]}"]'
                )
            ).to_be_visible(timeout=WAIT_MS)
        item_page.screenshot(
            path=str(evidence / f"completed-errors-item-{index}.png"), full_page=True
        )
        item_page.close()
    for index, ticket in enumerate(tickets[:3]):
        ticket_page = open_page(
            context, server, f"#/workspace/{ticket['id']}", '[data-screen="ticket"]'
        )
        ticket_page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
        ticket_page.locator('[data-conversation-lens-choice="full"]').click(timeout=WAIT_MS)
        failure = ticket_page.locator('[data-conversation-row="turn_ended"]')
        expect(failure).to_contain_text("Historical Worker failure retained", timeout=WAIT_MS)
        ticket_page.screenshot(
            path=str(evidence / f"completed-error-history-retained-{index}.png"), full_page=True
        )
        ticket_page.close()
