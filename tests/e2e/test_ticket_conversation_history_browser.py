"""A Ticket owns every conversation transcript, with only the active one writable.

This is the one browser/server boundary for history. The focused component tests own the
individual controls. Here a real Ticket detail and two real stored records prove that the
selector changes the transcript, crosses the read-only boundary, and returns to current.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle


def _store_conversation(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    created_at: int,
    request: str,
    reply: str,
) -> None:
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, "
        "workspace_folder, access, latest_sequence, created_at) "
        "VALUES (?, 'codex', 'gpt-5.6-sol', '/tmp/history-workspace', 'full', 3, ?)",
        (conversation_id, created_at),
    )
    events = (
        (
            1,
            "prompt",
            {"text": request, "sender_label": "owner", "mode": "run_when_free"},
        ),
        (2, "agent_message", {"text": reply}),
        (3, "turn_ended", {"ending": "completed", "error_summary": None}),
    )
    conn.executemany(
        "INSERT INTO conversation_events "
        "(conversation_id, sequence, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            (conversation_id, sequence, kind, json.dumps(payload), created_at + sequence)
            for sequence, kind, payload in events
        ],
    )


def test_ticket_history_opens_a_past_transcript_read_only_then_returns_to_current(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Conversation history boundary",
    )["id"]
    past_id = "conv_history_past"
    active_id = "conv_history_active"

    with sqlite3.connect(server.db_path) as conn:
        _store_conversation(
            conn,
            conversation_id=past_id,
            created_at=1_700_000_000,
            request="Past request marker",
            reply="Past reply marker",
        )
        _store_conversation(
            conn,
            conversation_id=active_id,
            created_at=1_700_000_100,
            request="Current request marker",
            reply="Current reply marker",
        )
        conn.executemany(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            ((past_id, ticket_id), (active_id, ticket_id)),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (active_id, ticket_id),
        )

    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{ticket_id}",
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    selector = page.get_by_label("Ticket conversation")
    selector.wait_for(timeout=WAIT_MS)
    assert selector.input_value() == "__current__"

    selector.select_option(past_id)
    past_pane = page.locator('[data-conversation-read-only-boundary="true"]')
    past_pane.wait_for(timeout=WAIT_MS)
    past_pane.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    past_pane.locator('[data-conversation-row="agent_message"]').get_by_text(
        "Past reply marker", exact=True
    ).wait_for(timeout=WAIT_MS)
    assert page.get_by_text("Current reply marker", exact=True).count() == 0
    for mutation_control in (
        "[data-conversation-input]",
        "[data-conversation-send]",
        "[data-conversation-picker-model]",
        "[data-conversation-stop]",
        "[data-conversation-held-discard]",
        "[data-conversation-held-promote]",
        "[data-conversation-new-arm]",
    ):
        assert past_pane.locator(mutation_control).count() == 0
    assert past_pane.get_by_role("button", name="Conversation options").count() == 0

    selector.select_option("__current__")
    page.locator('[data-conversation-read-only-boundary="true"]').wait_for(
        state="detached", timeout=WAIT_MS
    )
    page.locator('[data-conversation-row="agent_message"]').get_by_text(
        "Current reply marker", exact=True
    ).wait_for(timeout=WAIT_MS)
    assert page.get_by_text("Past reply marker", exact=True).count() == 0
    page.locator("[data-conversation-input]").wait_for(timeout=WAIT_MS)
    page.locator("[data-conversation-picker-model]").wait_for(timeout=WAIT_MS)
    page.get_by_role("button", name="Conversation options").wait_for(timeout=WAIT_MS)
