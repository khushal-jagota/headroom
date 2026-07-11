"""Browser-driven checks for server-owned live chat state."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import Page

WAIT_MS = 10_000
WORKER_PROMPT_TEXT = "Work this ticket step from the current system prompt."
HISTORICAL_PREVIEW_MESSAGE_ID = 10_001_001


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


def _update_running_turn_output(server, entity_id: str, label: str, output_text: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        row = conn.execute(
            "SELECT id FROM chat_turns WHERE entity_id = ? AND status = 'running'",
            (entity_id,),
        ).fetchone()
        assert row is not None
        turn_id = row[0]
        conn.execute(
            "UPDATE chat_turns SET activity_label = ?, output_text = ?, "
            "updated_at = updated_at + 1 WHERE id = ?",
            (label, output_text, turn_id),
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


def _seed_replaceable_preview_subtree_chat_message(
    server, entity_id: str, ticket_id: str
) -> tuple[int, dict[str, str], str]:
    original_relative_paths = {
        "markdown": "notes/stable-preview.md",
        "image": "images/stable-preview.png",
        "video": "media/stable-preview.mp4",
        "audio": "media/stable-preview.wav",
        "html": "pages/stable-preview.html",
        "download": "files/stable-preview.bin",
    }
    replacement_relative_path = "notes/replacement-preview.md"
    root = Path(server.db_path).parent / "files" / "tickets" / ticket_id
    for relative_path in (*original_relative_paths.values(), replacement_relative_path):
        (root / relative_path).parent.mkdir(parents=True, exist_ok=True)
    (root / original_relative_paths["markdown"]).write_text(
        "# Stable managed preview\n\nThis historical source does not change.",
        encoding="utf-8",
    )
    (root / original_relative_paths["image"]).write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLv"
            "AAAAAElFTkSuQmCC"
        )
    )
    (root / original_relative_paths["video"]).write_bytes(
        base64.b64decode(
        "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAANcbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAA"
        "AHgAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAgAAAod0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAHgAAAAAAAAAAAAA"
        "AAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0"
        "AAAAAAAAAAEAAAB4AAAEAAABAAAAAAH/bWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAyAAAACABVxAAAAAAALWhk"
        "bHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABqm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAA"
        "AAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAWpzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5h"
        "dmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2Mi4xMS4xMDAgbGlieDI2"
        "NAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAMg8SJZYAQAGaOvjyyLA/fj4"
        "AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAL7iAAAAAAAAABhzdHRzAAAAAAAAAAEAAAADAAACAAAAABRz"
        "dHNzAAAAAAAAAAEAAAABAAAAKGN0dHMAAAAAAAAAAwAAAAEAAAQAAAAAAQAABgAAAAABAAACAAAAABxzdHNjAAAA"
        "AAAAAAEAAAABAAAAAwAAAAEAAAAgc3RzegAAAAAAAAAAAAAAAwAAAsUAAAAMAAAADAAAABRzdGNvAAAAAAAAAAEA"
        "AAOMAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAA"
        "ACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2Mi4zLjEwMAAAAAhmcmVlAAAC5W1kYXQAAAKuBgX//6rcRem95tlI"
        "t5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY1IHIzMjIyIGIzNTYwNWEgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0g"
        "Q29weWxlZnQgMjAwMy0yMDI1IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczog"
        "Y2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0x"
        "IHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4"
        "OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVh"
        "ZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFj"
        "ZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9h"
        "ZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUw"
        "IGtleWludF9taW49MjUgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYg"
        "bWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEu"
        "NDAgYXE9MToxLjAwAIAAAAAPZYiEADP//vbsvgU2FMjBAAAACEGaImxCv/7AAAAACAGeQXkK/8SB"
    )
    )
    (root / original_relative_paths["audio"]).write_bytes(
        base64.b64decode(
        "UklGRoYGAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAATElTVBoAAABJTkZPSVNGVA0AAABMYXZmNjIuMy4x"
        "MDAAAGRhdGFABgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
    )
    )
    (root / original_relative_paths["html"]).write_text(
        "<!doctype html><html><body><h1>Stable HTML preview</h1></body></html>",
        encoding="utf-8",
    )
    (root / original_relative_paths["download"]).write_bytes(b"stable managed download\n")
    (root / replacement_relative_path).write_text(
        "# Replacement managed preview\n\nThis is a different managed file.",
        encoding="utf-8",
    )
    managed_paths = {
        kind: f"/files/tickets/{ticket_id}/{relative_path}"
        for kind, relative_path in original_relative_paths.items()
    }
    message = (
        "Historical preview subtree:\n\n"
        f"[Stable Markdown preview]({managed_paths['markdown']})\n\n"
        f"[Stable image preview]({managed_paths['image']})\n\n"
        f"[Stable video preview]({managed_paths['video']})\n\n"
        f"[Stable audio preview]({managed_paths['audio']})\n\n"
        f"[Stable HTML preview]({managed_paths['html']})\n\n"
        f"[Stable download preview]({managed_paths['download']})\n\n"
        "[Stable external preview](https://example.com/stable-preview)"
    )
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, entity_id, turn_id, role, text, created_at) "
            "VALUES (?, ?, NULL, 'assistant', ?, 0)",
            (HISTORICAL_PREVIEW_MESSAGE_ID, entity_id, message),
        )
    return (
        HISTORICAL_PREVIEW_MESSAGE_ID,
        managed_paths,
        f"/files/tickets/{ticket_id}/{replacement_relative_path}",
    )


def _replace_preview_subtree_in_historical_message(
    server, message_id: int, replacement_preview_path: str
) -> None:
    replacement_message = (
        "Historical preview subtree:\n\n"
        f"[Replacement preview]({replacement_preview_path})"
    )
    with sqlite3.connect(server.db_path) as conn:
        cursor = conn.execute(
            "UPDATE chat_messages SET text = ? WHERE id = ?",
            (replacement_message, message_id),
        )
        assert cursor.rowcount == 1


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


@pytest.mark.parametrize("chat_context", ["ticket", "chief"])
def test_running_chat_preserves_unchanged_preview_subtree_and_replaces_changed_target(
    server, context_factory, open_page, cli, chat_context
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Stable chat preview")["id"]
    if chat_context == "ticket":
        entity_id = ticket_id
        route = f"#/ticket/{ticket_id}"
        ready_selector = 'section[data-screen="ticket"] [data-chat] [data-chat-input]'
    else:
        entity_id = "agent_panels_chief_of_staff"
        route = "#/chief"
        ready_selector = 'section[data-screen="chief"] [data-chat-input]'

    message_id, managed_preview_paths, replacement_preview_path = (
        _seed_replaceable_preview_subtree_chat_message(server, entity_id, ticket_id)
    )
    if chat_context == "ticket":
        _seed_running_worker_turn(server, entity_id)
    else:
        _seed_running_chief_turn(server, entity_id)
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE chat_turns SET output_text = ? "
            "WHERE entity_id = ? AND status = 'running'",
            ("Live response **before**", entity_id),
        )

    requests: list[str] = []
    observed_preview_paths = {*managed_preview_paths.values(), replacement_preview_path}
    context = context_factory()
    context.on(
        "request",
        lambda request: requests.append(request.url)
        if any(request.url.endswith(path) for path in observed_preview_paths)
        else None,
    )
    page = open_page(
        context,
        server,
        route,
        ready_selector,
        settled=True,
    )
    historical_message = page.locator(
        '[data-chat-msg="planner"]', has_text="Historical preview subtree:"
    )
    expected_kinds = (
        "markdown",
        "image",
        "video",
        "audio",
        "html",
        "download",
        "external",
    )
    page.wait_for_function(
        "kinds => {"
        " const message = Array.from(document.querySelectorAll('[data-chat-msg=\"planner\"]'))"
        "   .find(node => node.textContent.includes('Historical preview subtree:'));"
        " return message && kinds.every(kind =>"
        "   message.querySelectorAll(`[data-file-preview-kind=\"${kind}\"]`).length === 1"
        " );"
        "}",
        arg=expected_kinds,
        timeout=WAIT_MS,
    )
    historical_message.scroll_into_view_if_needed(timeout=WAIT_MS)
    historical_message.locator('[data-file-preview-kind="markdown"] h1').wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert historical_message.locator(
        '[data-file-preview-kind="markdown"] h1'
    ).inner_text() == "Stable managed preview"
    image = historical_message.locator('[data-file-preview-kind="image"] img')
    image.scroll_into_view_if_needed(timeout=WAIT_MS)
    image.wait_for(state="visible", timeout=WAIT_MS)
    page.wait_for_function(
        "image => image.complete && image.naturalWidth === 1 && image.naturalHeight === 1",
        arg=image.element_handle(),
        timeout=WAIT_MS,
    )
    for media_kind in ("video", "audio"):
        media = historical_message.locator(
            f'[data-file-preview-kind="{media_kind}"] {media_kind}'
        )
        assert media.get_attribute("controls") is not None
        page.wait_for_function(
            "media => media.readyState > 0 && media.error === null",
            arg=media.element_handle(),
            timeout=WAIT_MS,
        )
    html_frame = historical_message.locator('[data-file-preview-kind="html"] iframe')
    html_frame.scroll_into_view_if_needed(timeout=WAIT_MS)
    historical_message.frame_locator('[data-file-preview-kind="html"] iframe').locator(
        "h1", has_text="Stable HTML preview"
    ).wait_for(state="visible", timeout=WAIT_MS)
    assert (
        historical_message.locator('[data-file-preview-kind="download"] a[download]').inner_text()
        == "Download"
    )
    external_preview = historical_message.locator('[data-file-preview-kind="external"]')
    assert "example.com" in external_preview.inner_text()
    assert external_preview.locator("a").get_attribute("href") == (
        "https://example.com/stable-preview"
    )
    page.locator('[data-chat-msg="planner"] strong', has_text="before").wait_for(
        state="visible", timeout=WAIT_MS
    )
    requests.clear()

    page.evaluate(
        "kinds => {"
        " const message = Array.from(document.querySelectorAll('[data-chat-msg=\"planner\"]'))"
        "   .find(node => node.textContent.includes('Historical preview subtree:'));"
        " window.__stableHistoricalPreviews = Object.fromEntries(kinds.map(kind => ["
        "   kind, message.querySelector(`[data-file-preview-kind=\"${kind}\"]`)"
        " ]));"
        " window.__stableHistoricalPreviewLoadingCount = 0;"
        " window.__stableHistoricalPreviewObserver = new MutationObserver(records => {"
        "   for (const record of records) {"
        "     for (const added of record.addedNodes) {"
        "       if ((added.textContent || '').includes('Loading preview...'))"
        "         window.__stableHistoricalPreviewLoadingCount += 1;"
        "     }"
        "   }"
        " });"
        " window.__stableHistoricalPreviewObserver.observe("
        "   document.querySelector('[data-chat-messages]'),"
        "   { childList: true, subtree: true }"
        " );"
        "}",
        expected_kinds,
    )

    for index in range(3):
        label = f"Unrelated live activity {index + 1}"
        _update_running_worker_turn_label(server, entity_id, label)
        _wait_chat_text(page, "worker" if chat_context == "ticket" else "planner", label)
        _wait_chat_text(page, "planner", "Live response before")
        page.wait_for_function(
            "kinds => {"
            " const message = Array.from(document.querySelectorAll('[data-chat-msg=\"planner\"]'))"
            "   .find(node => node.textContent.includes('Historical preview subtree:'));"
            " return message && kinds.every(kind => {"
            "   const original = window.__stableHistoricalPreviews[kind];"
            "   return original?.isConnected"
            "     && original === message.querySelector(`[data-file-preview-kind=\"${kind}\"]`);"
            " }) && window.__stableHistoricalPreviewLoadingCount === 0;"
            "}",
            arg=expected_kinds,
            timeout=WAIT_MS,
        )
        assert requests == []

    _update_running_turn_output(
        server,
        entity_id,
        "Live output changed",
        "Live response **after**",
    )
    page.locator('[data-chat-msg="planner"] strong', has_text="after").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert page.locator(
        '[data-chat-msg="planner"] strong', has_text="before"
    ).count() == 0
    assert requests == []
    assert page.evaluate("() => window.__stableHistoricalPreviewLoadingCount") == 0
    assert page.evaluate(
        "kinds => {"
        " const message = Array.from(document.querySelectorAll('[data-chat-msg=\"planner\"]'))"
        "   .find(node => node.textContent.includes('Historical preview subtree:'));"
        " return message && kinds.every(kind => {"
        "   const original = window.__stableHistoricalPreviews[kind];"
        "   return original.isConnected"
        "     && original === message.querySelector(`[data-file-preview-kind=\"${kind}\"]`);"
        " });"
        "}",
        expected_kinds,
    ) is True

    _replace_preview_subtree_in_historical_message(server, message_id, replacement_preview_path)
    replacement_preview = page.locator(
        '[data-chat-msg="planner"]', has_text="Historical preview subtree:"
    ).locator('[data-file-preview-kind="markdown"]')
    replacement_preview.locator("h1", has_text="Replacement managed preview").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.wait_for_function(
        "kinds => {"
        " const replacement = document.querySelector("
        "   '[data-chat-msg=\"planner\"] [data-file-preview-kind=\"markdown\"]'"
        " );"
        " return kinds.every(kind => !window.__stableHistoricalPreviews[kind].isConnected)"
        "   && replacement"
        "   && replacement !== window.__stableHistoricalPreviews.markdown"
        "   && document.querySelectorAll('[data-file-preview-kind]').length === 1;"
        "}",
        arg=expected_kinds,
        timeout=WAIT_MS,
    )
    assert historical_message.locator("h1", has_text="Stable managed preview").count() == 0
    for preview_path in managed_preview_paths.values():
        assert requests.count(server.base + preview_path) == 0
    assert requests.count(server.base + replacement_preview_path) == 1
    page.evaluate("() => window.__stableHistoricalPreviewObserver.disconnect()")


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
    entity_id = cli(server, "ticket", "create", "--title", "Activity scroll ticket")["id"]
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
    entity_id = cli(server, "ticket", "create", "--title", "Wheel activity scroll ticket")[
        "id"
    ]
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
    tid = cli(server, "ticket", "create", "--title", "Live worker state ticket")["id"]
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
