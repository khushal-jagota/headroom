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
            "INSERT INTO chat_turn_activity_entries ("
            "turn_id, action_identity, category, label, lifecycle_state, "
            "started_at, updated_at, completed_at"
            ") VALUES (?, 'thinking', 'thinking', 'Thinking', 'complete', 1, 1, 1), "
            "(?, 'tool:plan', 'tool', 'Checking the plan', 'running', 1, 1, NULL)",
            (turn_id, turn_id),
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
            "INSERT INTO chat_turn_activity_entries ("
            "turn_id, action_identity, category, label, lifecycle_state, "
            "started_at, updated_at, completed_at"
            ") VALUES (?, 'thinking', 'thinking', 'Thinking', 'complete', 1, 1, 1), "
            "(?, 'tool:workspace', 'tool', 'Reading workspace status', 'running', 1, 1, NULL)",
            (turn_id, turn_id),
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


def _seed_chat_history(server, entity_id: str, count: int = 80) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.executemany(
            "INSERT INTO chat_messages (entity_id, turn_id, role, text, created_at) "
            "VALUES (?, NULL, 'assistant', ?, 0)",
            [(entity_id, f"history line {index}") for index in range(count)],
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
            "INSERT INTO chat_turn_activity_entries ("
            "turn_id, action_identity, category, label, lifecycle_state, "
            "started_at, updated_at, completed_at"
            ") VALUES (?, ?, 'tool', ?, 'running', 2, 2, NULL)",
            (turn_id, f"tool:live-{label}", label),
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


def _seed_long_running_worker_activity(server, entity_id: str, count: int = 40) -> None:
    with sqlite3.connect(server.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM chat_turns WHERE entity_id = ? AND status = 'running'",
            (entity_id,),
        ).fetchone()
        assert row is not None
        turn_id = row[0]
        conn.executemany(
            "INSERT INTO chat_turn_activity_entries ("
            "turn_id, action_identity, category, label, lifecycle_state, "
            "started_at, updated_at, completed_at"
            ") VALUES (?, ?, 'tool', ?, 'complete', ?, ?, ?)",
            [
                (
                    turn_id,
                    f"tool:seed-{index}",
                    f"Seeded activity {index:02d}",
                    10 + index,
                    10 + index,
                    10 + index,
                )
                for index in range(count)
            ],
        )


def _chat_scroll_state(page: Page, selector: str) -> dict:
    return page.eval_on_selector(
        selector,
        "el => ({ top: el.scrollTop, max: el.scrollHeight - el.clientHeight })",
    )


def _wait_chat_at_bottom(page: Page, selector: str) -> None:
    page.wait_for_function(
        "selector => { const el = document.querySelector(selector); "
        "return el && Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) <= 1; }",
        arg=selector,
        timeout=WAIT_MS,
    )


def _wheel_up_inside_chat_thread(page: Page, selector: str, delta_y: int = -24) -> None:
    box = page.locator(selector).bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    before = _chat_scroll_state(page, selector)
    page.mouse.wheel(0, delta_y)
    page.wait_for_function(
        "({ selector, beforeTop }) => {"
        " const el = document.querySelector(selector);"
        " return el && el.scrollTop < beforeTop;"
        "}",
        arg={"selector": selector, "beforeTop": before["top"]},
        timeout=WAIT_MS,
    )


def test_ticket_chat_send_survives_navigation_from_server_state(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--type",
        "coding",
        "--title",
        "Live chat remount ticket",
    )["id"]
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
    assert [entry["label"] for entry in state["active_turn"]["activity_entries"]] == [
        "Thinking",
        "Reading workspace status",
    ]

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
    toggle = page.locator("[data-chat-activity-toggle]")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.locator("[data-chat-activity-details]").count() == 0
    toggle.press("Enter")
    page.wait_for_selector("[data-chat-activity-details]", timeout=WAIT_MS)
    assert toggle.get_attribute("aria-expanded") == "true"
    details = page.locator("[data-chat-activity-details]")
    assert "Thinking" in details.inner_text()
    assert "Reading workspace status" in details.inner_text()
    assert page.locator("[data-chat-activity-entry]").count() == 2
    assert page.locator("[data-chat-send]").get_attribute("title") == "Pause"
    assert page.locator("[data-chat-send]").is_enabled()
    assert page.locator("[data-chat-input]").is_enabled()
    page.fill("[data-chat-input]", "draft while chief works")
    assert page.locator("[data-chat-input]").input_value() == "draft while chief works"

    _update_running_worker_turn_label(server, entity_id, "Checking ticket activity")
    _wait_chat_text(page, "planner", "Checking ticket activity")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-chat-activity-entry]').length === 3",
        timeout=WAIT_MS,
    )
    assert "Checking ticket activity" in details.inner_text()
    assert page.locator("[data-chat-input]").input_value() == "draft while chief works"
    toggle.press("Enter")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.locator("[data-chat-activity-details]").count() == 0
    assert page.locator("[data-chat-input]").input_value() == "draft while chief works"

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + "/#/chief")
    page.wait_for_selector('section[data-screen="chief"] [data-chat-input]', timeout=WAIT_MS)
    _wait_chat_text(page, "you", "What needs attention?")
    _wait_chat_text(page, "planner", "I found the current board.")
    _wait_chat_text(page, "planner", "Checking ticket activity")
    page.wait_for_selector('[data-chat-pending] [data-chat-activity]', timeout=WAIT_MS)
    assert page.locator("[data-chat-activity-toggle]").get_attribute("aria-expanded") == "false"
    assert page.locator("[data-chat-activity-details]").count() == 0


def test_activity_growth_respects_existing_chat_follow_mode(
    server, context_factory, open_page, cli
) -> None:
    entity_id = cli(
        server,
        "ticket",
        "create",
        "--type",
        "coding",
        "--title",
        "Activity scroll ticket",
    )["id"]
    _seed_running_worker_turn(server, entity_id)
    _seed_chat_history(server, entity_id)
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{entity_id}",
        'section[data-screen="ticket"] [data-chat-activity-toggle]',
        settled=True,
    )
    thread_selector = "[data-chat] [data-chat-messages]"
    page.wait_for_function(
        "selector => { const el = document.querySelector(selector); "
        "return el && el.scrollHeight > el.clientHeight; }",
        arg=thread_selector,
        timeout=WAIT_MS,
    )
    page.locator("[data-chat-activity-toggle]").click()
    page.wait_for_selector("[data-chat-activity-details]", timeout=WAIT_MS)
    page.wait_for_function(
        "selector => { const el = document.querySelector(selector); "
        "return el && Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) <= 1; }",
        arg=thread_selector,
        timeout=WAIT_MS,
    )

    page.eval_on_selector(
        thread_selector,
        "el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }",
    )
    page.wait_for_selector("[data-chat-jump]", timeout=WAIT_MS)
    _update_running_worker_turn_label(server, entity_id, "Inspecting another activity")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-chat-activity-entry]').length === 3",
        timeout=WAIT_MS,
    )
    assert page.eval_on_selector(thread_selector, "el => el.scrollTop") == 0
    assert page.locator("[data-chat-jump]").is_visible()


def test_expanded_activity_live_updates_preserve_real_wheel_scrollback(
    server, context_factory, open_page, cli
) -> None:
    entity_id = cli(
        server,
        "ticket",
        "create",
        "--type",
        "coding",
        "--title",
        "Wheel activity scroll ticket",
    )["id"]
    _seed_running_worker_turn(server, entity_id)
    _seed_chat_history(server, entity_id, count=30)
    _seed_long_running_worker_activity(server, entity_id, count=50)

    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{entity_id}",
        'section[data-screen="ticket"] [data-chat-activity-toggle]',
        settled=True,
    )
    page.add_style_tag(
        content="[data-chat-messages] { flex: 0 0 220px !important; }"
    )
    thread_selector = "[data-chat] [data-chat-messages]"
    jump_selector = "[data-chat] [data-chat-jump]"
    page.locator("[data-chat-activity-toggle]").click()
    page.wait_for_selector("[data-chat-activity-details]", timeout=WAIT_MS)
    page.wait_for_function(
        "() => document.querySelectorAll('[data-chat-activity-entry]').length >= 52",
        timeout=WAIT_MS,
    )
    _wait_chat_at_bottom(page, thread_selector)

    for index in range(3):
        label = f"Live wheel activity {index:02d}"
        _update_running_worker_turn_label(server, entity_id, label)
        _wait_chat_text(page, "worker", label)
        _wait_chat_at_bottom(page, thread_selector)

    _wheel_up_inside_chat_thread(page, thread_selector)
    scrolled_back = _chat_scroll_state(page, thread_selector)
    assert scrolled_back["top"] < scrolled_back["max"], scrolled_back

    for index in range(3, 7):
        label = f"Live wheel activity {index:02d}"
        _update_running_worker_turn_label(server, entity_id, label)
        _wait_chat_text(page, "worker", label)
        after_update = _chat_scroll_state(page, thread_selector)
        assert after_update["top"] == scrolled_back["top"], {
            "before_live_updates": scrolled_back,
            "after_update": after_update,
            "label": label,
        }

    page.wait_for_selector(jump_selector, timeout=WAIT_MS)
    assert page.locator(jump_selector).is_visible()

    page.eval_on_selector(
        thread_selector,
        "el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll')); }",
    )
    page.wait_for_function(
        "selector => document.querySelector(selector) === null",
        arg=jump_selector,
        timeout=WAIT_MS,
    )
    resume_label = "Live wheel activity 07"
    _update_running_worker_turn_label(server, entity_id, resume_label)
    _wait_chat_text(page, "worker", resume_label)
    _wait_chat_at_bottom(page, thread_selector)


def test_ticket_chat_shows_running_worker_turn_after_remount(
    server, context_factory, open_page, cli, api
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--type",
        "coding",
        "--title",
        "Live worker state ticket",
    )["id"]
    _seed_running_worker_turn(server, tid)

    state = api.get(server, f"/api/chat/{tid}/state")
    assert state["active_turn"]["origin"] == "worker"
    assert state["active_turn"]["phase"] == "doing"
    assert state["active_turn"]["activity_label"] == "Checking the plan"
    assert [entry["label"] for entry in state["active_turn"]["activity_entries"]] == [
        "Thinking",
        "Checking the plan",
    ]

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
    ticket_toggle = page.locator("[data-chat] [data-chat-activity-toggle]")
    assert ticket_toggle.get_attribute("aria-expanded") == "false"
    ticket_toggle.press("Space")
    page.wait_for_selector("[data-chat] [data-chat-activity-details]", timeout=WAIT_MS)
    assert "Checking the plan" in page.locator(
        "[data-chat] [data-chat-activity-details]"
    ).inner_text()
    ticket_toggle.press("Space")
    assert page.locator("[data-chat] [data-chat-activity-details]").count() == 0
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
    tid = cli(
        server,
        "ticket",
        "create",
        "--type",
        "coding",
        "--title",
        "Pause visible chat turn",
    )["id"]
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
    tid = cli(
        slow_server,
        "ticket",
        "create",
        "--type",
        "coding",
        "--title",
        "Pause then send ticket",
    )["id"]
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
