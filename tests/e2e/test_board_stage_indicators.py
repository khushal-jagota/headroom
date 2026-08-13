"""Focused Workspace status-group and row-signal regressions."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle

from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationEventPayload,
    ConversationTurnEnding,
    TurnEndedEventPayload,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore


def _add_today(api: ApiHelper, server: ServerHandle, ticket_id: str) -> None:
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})


def _set_ticket_status(
    server: ServerHandle, ticket_id: str, status: str, *, backend_error: str | None = None
) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status = ?, backend_error = ? WHERE id = ?",
            (status, backend_error, ticket_id),
        )


def _set_ticket_stage(server: ServerHandle, ticket_id: str, stage: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET stage = ? WHERE id = ?",
            (stage, ticket_id),
        )



def _create_ticket(
    cli: Callable[..., JsonObject],
    server: ServerHandle,
    title: str,
    *,
    worker_type: str = "coding",
    project_id: str = "project_vylo",
) -> str:
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        worker_type,
        "--title",
        title,
        "--project-id",
        project_id,
    )["id"]
    return ticket_id


def _start_conversation(server: ServerHandle, conversation_id: str) -> None:
    """Make a conversation for the board to draw a mark against.

    Every conversation is started on a named model, so this names one. It is a name rather
    than a real model because nothing here ever spawns a backend: what this file is about
    is the reply mark, and the conversation is the thing the mark hangs on.
    """
    created = httpx.post(
        f"{server.base}/api/conversation/conversations",
        json={
            "conversation_id": conversation_id,
            "backend_key": "codex",
            "model": "e2e-model",
        },
        timeout=10.0,
    )
    assert created.status_code == 201, created.text


def _append_rows(
    server: ServerHandle, conversation_id: str, *payloads: ConversationEventPayload
) -> None:
    """Write rows into the record, exactly as the conversation system writes them.

    The store's calls are awaited and this thread belongs to the browser driver, so the
    writing happens on a thread of its own and this one waits for it.
    """
    store = ConversationStore(str(server.db_path))

    async def write() -> None:
        for payload in payloads:
            await store.append_event(conversation_id, payload)

    fell_over: list[BaseException] = []

    def run_it() -> None:
        try:
            asyncio.run(write())
        except BaseException as error:  # noqa: BLE001 - re-raised on the calling thread
            fell_over.append(error)

    writer = threading.Thread(target=run_it)
    writer.start()
    writer.join()
    if fell_over:
        raise fell_over[0]


def _link_conversation(server: ServerHandle, ticket_id: str, conversation_id: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (conversation_id, ticket_id),
        )


def test_workspace_reply_mark_follows_the_record_and_what_this_browser_has_read(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    conversation_id = "conv-workspace-reply"
    ticket_id = _create_ticket(cli, server, "Reply mark Workspace ticket")
    _add_today(api, server, ticket_id)
    _set_ticket_stage(server, ticket_id, "needs_success")
    # Keep the row in the default attention view. This test owns the conversation
    # mark boundary, not the quiet-row disclosure boundary.
    _set_ticket_status(server, ticket_id, "user")
    _start_conversation(server, conversation_id)
    _link_conversation(server, ticket_id, conversation_id)

    page = open_page(
        context_factory(),
        server,
        "#/workspace",
        f'[data-card][data-ticket-id="{ticket_id}"]',
    )
    mark = f'[data-card][data-ticket-id="{ticket_id}"] .board-workspace-stage-mark'
    # A conversation whose turns have never ended has nothing waiting for anybody.
    assert page.get_attribute(mark, "data-latest-turn-ended") == "0"
    assert page.get_attribute(mark, "data-stage-state") == "upcoming"
    assert page.get_attribute(mark, "aria-label") == "Nothing waiting"

    # A turn ending is a reply waiting. The rows are written from this process, so the
    # server's own change signal never hears them and the screen is reloaded rather than
    # pretending it would light up on its own.
    _append_rows(
        server,
        conversation_id,
        AgentMessageEventPayload(content=text_message_content("the first answer")),
        TurnEndedEventPayload(ending=ConversationTurnEnding.completed),
    )
    page.reload()
    page.wait_for_selector(mark, timeout=WAIT_MS)
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'current-awaiting-approval'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-latest-turn-ended") == "2"
    assert page.get_attribute(mark, "aria-label") == "Unseen agent reply"

    # Opening the Ticket is reading it. Reading writes nothing the server announces, so
    # the row goes quiet on this browser's own account, without waiting for a refetch.
    page.click(f'[data-card][data-ticket-id="{ticket_id}"]')
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'reply-seen'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "aria-label") == "Agent reply seen"

    # A reply seen is a POSITION, not a flag: leave the Ticket, let a second turn end
    # past where this browser read, and the row is waiting again.
    page.goto(server.base + "/#/workspace")
    page.wait_for_selector(mark, timeout=WAIT_MS)
    _append_rows(
        server,
        conversation_id,
        AgentMessageEventPayload(content=text_message_content("the second answer")),
        TurnEndedEventPayload(ending=ConversationTurnEnding.completed),
    )
    page.reload()
    page.wait_for_selector(mark, timeout=WAIT_MS)
    page.wait_for_function(
        "selector => document.querySelector(selector)?.getAttribute('data-stage-state') "
        "=== 'current-awaiting-approval'",
        arg=mark,
        timeout=WAIT_MS,
    )
    assert page.get_attribute(mark, "data-latest-turn-ended") == "4"
