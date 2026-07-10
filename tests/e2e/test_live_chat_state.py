"""Browser-driven checks for server-owned live chat state."""

from __future__ import annotations

import json
import sqlite3

from playwright.sync_api import Page

WAIT_MS = 10_000
WORKER_PROMPT_TEXT = "Work this ticket step from the current system prompt."


def _wait_chat_text(page: Page, who: str, text: str) -> None:
    page.wait_for_function(
        "({ who, text }) => Array.from(document.querySelectorAll(`[data-chat-msg=\"${who}\"]`))"
        ".some(el => el.textContent.includes(text))",
        arg={"who": who, "text": text},
        timeout=WAIT_MS,
    )


def _seed_running_worker_turn(server, entity_id: str) -> None:
    turn_id = f"run_e2e_worker_{entity_id}"
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "INSERT INTO chat_turns ("
            "id, entity_id, origin, mode, status, phase, activity_label, output_role, "
            "output_text, session_key, error, started_at, updated_at, completed_at"
            ") VALUES (?, ?, 'worker', 'worker_step', 'running', 'doing', ?, "
            "'assistant', '', 'worker-session-e2e', NULL, 1, 1, NULL)",
            (turn_id, entity_id, "Checking the plan"),
        )
        conn.execute(
            "INSERT INTO chat_messages (entity_id, turn_id, role, text, created_at) "
            "VALUES (?, ?, 'worker', ?, 1)",
            (entity_id, turn_id, WORKER_PROMPT_TEXT),
        )
        conn.execute(
            "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, 1)",
            (
                entity_id,
                "chat_turn_started",
                json.dumps(
                    {
                        "turn_id": turn_id,
                        "origin": "worker",
                        "mode": "worker_step",
                        "phase": "doing",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, 1)",
            (
                entity_id,
                "chat_turn_updated",
                json.dumps(
                    {
                        "turn_id": turn_id,
                        "phase": "doing",
                        "activity_label": "Checking the plan",
                    }
                ),
            ),
        )


def _seed_running_chief_turn(server, entity_id: str) -> None:
    turn_id = f"run_e2e_chief_{entity_id}"
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agent_chat_sessions "
            "(id, chat_session_key, created_at, updated_at) VALUES (?, ?, 1, 1)",
            (entity_id, "chief-session-e2e"),
        )
        conn.execute(
            "INSERT INTO chat_turns ("
            "id, entity_id, origin, mode, status, phase, activity_label, output_role, "
            "output_text, session_key, error, started_at, updated_at, completed_at"
            ") VALUES (?, ?, 'human', 'message', 'running', 'doing', ?, "
            "'assistant', 'I found the current board. ', 'chief-session-e2e', NULL, 1, 1, NULL)",
            (turn_id, entity_id, "Reading workspace status"),
        )
        conn.execute(
            "INSERT INTO chat_messages (entity_id, turn_id, role, text, created_at) "
            "VALUES (?, ?, 'human', ?, 1)",
            (entity_id, turn_id, "What needs attention?"),
        )
        conn.execute(
            "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, 1)",
            (
                entity_id,
                "chat_turn_started",
                json.dumps(
                    {
                        "turn_id": turn_id,
                        "origin": "human",
                        "mode": "message",
                        "phase": "doing",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, 1)",
            (
                entity_id,
                "chat_turn_updated",
                json.dumps(
                    {
                        "turn_id": turn_id,
                        "phase": "doing",
                        "activity_label": "Reading workspace status",
                    }
                ),
            ),
        )


def _update_running_worker_turn_label(server, entity_id: str, label: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM chat_turns WHERE entity_id = ? AND status = 'running'",
            (entity_id,),
        ).fetchone()
        assert row is not None
        turn_id = row[0]
        conn.execute(
            "UPDATE chat_turns SET activity_label = ?, updated_at = updated_at + 1 "
            "WHERE id = ?",
            (label, turn_id),
        )
        conn.execute(
            "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, 2)",
            (
                entity_id,
                "chat_turn_updated",
                json.dumps(
                    {
                        "turn_id": turn_id,
                        "phase": "doing",
                        "activity_label": label,
                    }
                ),
            ),
        )


def test_ticket_chat_send_survives_navigation_from_server_state(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(server, "ticket", "create", "--title", "Live chat remount ticket")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )

    page.fill("[data-chat] [data-chat-input]", "persist this ticket message")
    page.click("[data-chat] [data-chat-send]")
    _wait_chat_text(page, "you", "persist this ticket message")
    _wait_chat_text(page, "planner", "echo: persist this ticket message")

    state = api.get(server, f"/api/chat/{tid}/state")
    assert [msg["text"] for msg in state["messages"]] == [
        "persist this ticket message",
        "echo: persist this ticket message",
    ]

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + f"/#/ticket/{tid}")
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    _wait_chat_text(page, "you", "persist this ticket message")
    _wait_chat_text(page, "planner", "echo: persist this ticket message")

    fresh_page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    _wait_chat_text(fresh_page, "you", "persist this ticket message")
    _wait_chat_text(fresh_page, "planner", "echo: persist this ticket message")


def test_chief_chat_send_survives_navigation_from_server_state(
    server, context_factory, open_page, api
) -> None:
    entity_id = "agent_panels_chief_of_staff"
    page = open_page(
        context_factory(),
        server,
        "#/chief",
        'section[data-screen="chief"] [data-chat-input]',
        settled=False,
    )

    page.fill("[data-chat-input]", "remember this chief message")
    page.click("[data-chat-send]")
    _wait_chat_text(page, "you", "remember this chief message")
    _wait_chat_text(page, "planner", "echo: remember this chief message")

    state = api.get(server, f"/api/chat/{entity_id}/state")
    assert [msg["text"] for msg in state["messages"]] == [
        "remember this chief message",
        "echo: remember this chief message",
    ]

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + "/#/chief")
    page.wait_for_selector('section[data-screen="chief"] [data-chat-input]', timeout=WAIT_MS)
    _wait_chat_text(page, "you", "remember this chief message")
    _wait_chat_text(page, "planner", "echo: remember this chief message")

    fresh_page = open_page(
        context_factory(),
        server,
        "#/chief",
        'section[data-screen="chief"] [data-chat-input]',
        settled=True,
    )
    _wait_chat_text(fresh_page, "you", "remember this chief message")
    _wait_chat_text(fresh_page, "planner", "echo: remember this chief message")


def test_chief_chat_shows_running_activity_status_after_remount(
    server, context_factory, open_page, api
) -> None:
    entity_id = "agent_panels_chief_of_staff"
    _seed_running_chief_turn(server, entity_id)

    state = api.get(server, f"/api/chat/{entity_id}/state")
    assert state["active_turn"]["origin"] == "human"
    assert state["active_turn"]["phase"] == "doing"
    assert state["active_turn"]["activity_label"] == "Reading workspace status"

    page = open_page(
        context_factory(),
        server,
        "#/chief",
        'section[data-screen="chief"] [data-chat-input]',
        settled=True,
    )
    _wait_chat_text(page, "you", "What needs attention?")
    _wait_chat_text(page, "planner", "I found the current board.")
    _wait_chat_text(page, "planner", "Reading workspace status")
    page.wait_for_selector('[data-chat-pending] [data-chat-activity]', timeout=WAIT_MS)
    assert page.locator("[data-chat-send]").get_attribute("title") == "Pause"
    assert page.locator("[data-chat-send]").is_enabled()
    assert page.locator("[data-chat-input]").is_enabled()
    page.fill("[data-chat-input]", "draft while chief works")
    assert page.locator("[data-chat-input]").input_value() == "draft while chief works"

    _update_running_worker_turn_label(server, entity_id, "Checking ticket activity")
    _wait_chat_text(page, "planner", "Checking ticket activity")
    assert page.locator("[data-chat-input]").input_value() == "draft while chief works"

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + "/#/chief")
    page.wait_for_selector('section[data-screen="chief"] [data-chat-input]', timeout=WAIT_MS)
    _wait_chat_text(page, "you", "What needs attention?")
    _wait_chat_text(page, "planner", "I found the current board.")
    _wait_chat_text(page, "planner", "Checking ticket activity")
    page.wait_for_selector('[data-chat-pending] [data-chat-activity]', timeout=WAIT_MS)


def test_ticket_chat_shows_running_worker_turn_after_remount(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(server, "ticket", "create", "--title", "Live worker state ticket")["id"]
    _seed_running_worker_turn(server, tid)

    state = api.get(server, f"/api/chat/{tid}/state")
    assert state["active_turn"]["origin"] == "worker"
    assert state["active_turn"]["phase"] == "doing"
    assert state["active_turn"]["activity_label"] == "Checking the plan"

    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    _wait_chat_text(page, "worker", WORKER_PROMPT_TEXT)
    _wait_chat_text(page, "worker", "Checking the plan")
    page.wait_for_selector("[data-chat] [data-chat-pending]", timeout=WAIT_MS)
    assert page.locator("[data-chat] [data-chat-send]").get_attribute("title") == "Pause"

    assert page.locator("[data-chat] [data-chat-input]").is_enabled()
    page.fill("[data-chat] [data-chat-input]", "draft while worker runs")
    assert page.locator("[data-chat] [data-chat-input]").input_value() == "draft while worker runs"
    assert page.locator("[data-chat] [data-chat-send]").is_enabled()

    page.press("[data-chat] [data-chat-input]", "Enter")
    state = api.get(server, f"/api/chat/{tid}/state")
    assert [msg["text"] for msg in state["messages"]] == [WORKER_PROMPT_TEXT]

    _update_running_worker_turn_label(server, tid, "Still checking the plan")
    _wait_chat_text(page, "worker", "Still checking the plan")
    assert page.locator("[data-chat] [data-chat-input]").input_value() == "draft while worker runs"

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + f"/#/ticket/{tid}")
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    _wait_chat_text(page, "worker", WORKER_PROMPT_TEXT)
    _wait_chat_text(page, "worker", "Still checking the plan")
    page.wait_for_selector("[data-chat] [data-chat-pending]", timeout=WAIT_MS)
    assert page.locator("[data-chat] [data-chat-input]").is_enabled()
    assert page.locator("[data-chat] [data-chat-send]").get_attribute("title") == "Pause"
    page.fill("[data-chat] [data-chat-input]", "draft after remount")
    assert page.locator("[data-chat] [data-chat-input]").input_value() == "draft after remount"
    assert page.locator("[data-chat] [data-chat-send]").is_enabled()


def test_ticket_chat_pause_settles_visible_active_turn(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(server, "ticket", "create", "--title", "Pause visible chat turn")["id"]
    _seed_running_worker_turn(server, tid)

    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-send][title="Pause"]',
        settled=True,
    )
    _wait_chat_text(page, "worker", WORKER_PROMPT_TEXT)
    page.click('[data-chat] [data-chat-send][title="Pause"]')
    page.wait_for_function(
        "() => !document.querySelector('[data-chat] [data-chat-pending]')",
        timeout=WAIT_MS,
    )

    state = api.get(server, f"/api/chat/{tid}/state")
    assert state["active_turn"] is None
    assert [msg["text"] for msg in state["messages"]] == [WORKER_PROMPT_TEXT]


def test_ticket_chat_pause_then_immediate_send_keeps_one_new_reply_after_remount(
    server_factory, context_factory, open_page, cli, api
) -> None:
    slow_server = server_factory(gateway="slow_fake")
    tid = cli(slow_server, "ticket", "create", "--title", "Pause then send ticket")["id"]
    page = open_page(
        context_factory(),
        slow_server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )

    page.fill("[data-chat] [data-chat-input]", "first slow turn")
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_selector(
        '[data-chat] [data-chat-send][title="Pause"]', timeout=WAIT_MS
    )
    page.click('[data-chat] [data-chat-send][title="Pause"]')
    page.wait_for_function(
        "() => !document.querySelector('[data-chat] [data-chat-pending]')",
        timeout=WAIT_MS,
    )

    page.fill("[data-chat] [data-chat-input]", "second immediate turn")
    page.click("[data-chat] [data-chat-send]")
    _wait_chat_text(page, "planner", "echo: second immediate turn")
    page.wait_for_function(
        "() => !document.querySelector('[data-chat] [data-chat-pending]')",
        timeout=WAIT_MS,
    )

    page.goto(slow_server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(slow_server.base + f"/#/ticket/{tid}")
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    _wait_chat_text(page, "planner", "echo: second immediate turn")
    state = api.get(slow_server, f"/api/chat/{tid}/state")
    assert [
        message["text"]
        for message in state["messages"]
        if message["role"] == "assistant" and "second immediate turn" in message["text"]
    ] == ["echo: second immediate turn"]
