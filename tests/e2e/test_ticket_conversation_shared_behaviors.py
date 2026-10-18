"""Ticket browser adapters through the real core and controlled backend.

A mocked pane cannot prove that browser writes reach canonical queue/answer
writers, or that contentless SSE refreshes a second mounted Ticket pane.
Only the backend wire is scripted. No installed provider participates.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle
from tests.e2e.test_conversation_signals import (  # noqa: F401
    _post,
    signal_server,
)


def _create_ticket_conversation(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    *,
    conversation_id: str,
    title: str,
) -> str:
    ticket_id: str = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", title,
    )["id"]
    _post(server, f"/api/test/signals/{conversation_id}/start")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket_id),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (conversation_id, ticket_id),
        )
    return ticket_id


def _open_ticket_conversation(
    server: ServerHandle,
    ticket_id: str,
    open_page: Callable[..., Page],
    context: BrowserContext,
    ready_selector: str,
) -> Page:
    page = open_page(
        context, server, f"#/workspace/{ticket_id}",
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_conversation_layer(page)
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    return page


def _open_conversation_layer(page: Page) -> None:
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="opened"]').wait_for(timeout=WAIT_MS)


def test_agent_questions_survive_reload_submit_as_one_map_and_replay_answers(
    signal_server: ServerHandle,  # noqa: F811 - imported pytest fixture
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Browser answers reach the backend sink and survive durable replay."""
    server = signal_server
    conversation_id = "e2e-ticket-agent-questions"
    ticket_id = _create_ticket_conversation(
        server, cli, conversation_id=conversation_id, title="Answer agent questions",
    )
    _post(server, f"/api/test/signals/{conversation_id}/questions")
    context = context_factory()
    page = _open_ticket_conversation(
        server, ticket_id, open_page, context, "[data-user-input-panel]",
    )
    assert "Approve" not in page.locator("[data-user-input-panel]").inner_text()
    page.reload()
    page.wait_for_selector("[data-conversation-rest-bar]", timeout=WAIT_MS)
    _open_conversation_layer(page)
    page.wait_for_selector("[data-user-input-panel]", timeout=WAIT_MS)
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.locator('[data-user-input-option="Composer"]').click()
    page.locator('[data-user-input-option="Transcript"]').click()
    page.locator("[data-user-input-continue]").click()
    page.locator("[data-user-input-other]").fill("Recorded event replay")
    with page.expect_request(
        lambda request: request.url.endswith("/user-input-answers")
        and request.method == "POST",
    ) as submitted:
        with page.expect_response(
            lambda response: response.url.endswith("/user-input-answers")
            and response.request.method == "POST",
        ) as answered:
            page.locator("[data-user-input-continue]").click()
    expected_answers = {
        "scope": {"answers": ["Composer", "Transcript"]},
        "proof": {"answers": ["Recorded event replay"]},
    }
    assert submitted.value.post_data_json == {
        "request_id": "input-1", "answers": expected_answers,
    }
    assert answered.value.json() == {"landed": True}
    receipts = httpx.get(
        server.base + f"/api/test/signals/{conversation_id}/answers", timeout=10,
    )
    assert receipts.is_success, receipts.text
    assert receipts.json() == {"input-1": expected_answers}
    events = httpx.get(
        server.base + f"/api/conversation/conversations/{conversation_id}/events", timeout=10,
    ).json()["events"]
    assert [event["payload"] for event in events if event["kind"] == "user_input_answered"] == [
        {"request_id": "input-1", "answers": expected_answers},
    ]
    page.wait_for_selector('[data-conversation-user-input-state="answered"]', timeout=WAIT_MS)
    assert "Recorded event replay" in page.locator(
        "[data-conversation-user-input-answers]",
    ).inner_text()
    # A new document consumes the durable answered event, not local submitted state.
    page.reload()
    page.wait_for_selector("[data-conversation-rest-bar]", timeout=WAIT_MS)
    _open_conversation_layer(page)
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.wait_for_selector('[data-conversation-user-input-state="answered"]', timeout=WAIT_MS)
    assert "Recorded event replay" in page.locator(
        "[data-conversation-user-input-answers]",
    ).inner_text()
    expect(page.locator("[data-user-input-panel]")).to_have_count(0)


def test_canonical_queue_writes_refresh_another_mounted_tab(
    signal_server: ServerHandle,  # noqa: F811 - imported pytest fixture
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Actual held IDs reach real writers and SSE refreshes the other tab."""
    server = signal_server
    conversation_id = "e2e-ticket-canonical-queue"
    ticket_id = _create_ticket_conversation(
        server, cli, conversation_id=conversation_id, title="Manage held prompts",
    )
    path = f"/api/conversation/conversations/{conversation_id}"
    _post(server, f"/api/test/signals/{conversation_id}/queue")
    view = httpx.get(server.base + path, timeout=10).json()
    held = view["held_prompts"]
    assert [row["text"] for row in held] == ["queued message 1", "queued message 2"]
    discard_id, promote_id = [row["held_prompt_id"] for row in held]
    context = context_factory()
    first, second = [
        _open_ticket_conversation(
            server, ticket_id, open_page, context, "[data-conversation-held-stack]",
        ) for _ in range(2)
    ]
    for page in (first, second):
        expect(page.locator("[data-conversation-held-row]")).to_have_count(2)
    with first.expect_response(
        lambda response: response.url.endswith(f"/held-prompts/{discard_id}")
        and response.request.method == "DELETE",
    ) as discarded:
        first.locator(f'[data-conversation-held-discard="{discard_id}"]').click()
    assert discarded.value.json() == {"discarded": True}
    expect(second.locator("[data-conversation-held-row]")).to_have_count(1, timeout=WAIT_MS)
    assert httpx.get(server.base + path, timeout=10).json()["held_prompts"][0][
        "held_prompt_id"
    ] == promote_id
    with first.expect_response(
        lambda response: response.url.endswith(f"/held-prompts/{promote_id}/promote")
        and response.request.method == "POST",
    ) as promoted:
        first.locator(
            f'[data-conversation-held-promote="send_now"][data-held-prompt-id="{promote_id}"]',
        ).click()
    assert promoted.value.request.post_data_json == {"mode": "send_now"}
    assert promoted.value.json() == {"promoted": True, "fate": "started"}
    expect(second.locator("[data-conversation-held-row]")).to_have_count(0, timeout=WAIT_MS)
    assert httpx.get(server.base + path, timeout=10).json()["held_prompts"] == []
    events = httpx.get(server.base + path + "/events", timeout=10).json()["events"]
    assert any(
        event["kind"] == "prompt_discarded"
        and event["payload"].get("sender_message_id") == "sender-1"
        for event in events
    )
    assert any(
        event["kind"] == "prompt"
        and event["payload"].get("sender_message_id") == "sender-2"
        and event["payload"]["mode"] == "send_now"
        for event in events
    )
