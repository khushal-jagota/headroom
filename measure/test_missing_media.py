"""A managed video that is not there. When does the reader find out?"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle


def test_missing_media(server: ServerHandle, context_factory: Callable[[], BrowserContext],
                       cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Missing media")["id"]
    owner = {"kind": "owner", "id": "owner"}
    me = {"kind": "ticket", "id": ticket_id}
    import json
    events = [
        (1, "prompt", {"text": "show me", "sender_label": "owner", "mode": "queue",
                       "sender": owner, "recipient": me}),
        (2, "message_to_owner", {
            "text": f"Here.\n\n[gone.mp4](/files/tickets/{ticket_id}/video/gone.mp4)\n\n"
                    f"![gone.png](/files/tickets/{ticket_id}/images/gone.png)",
            "sender_label": "Coding worker", "sender": me, "recipient": owner}),
        (3, "turn_ended", {"ending": "completed", "error_summary": None}),
    ]
    with sqlite3.connect(server.db_path) as conn:
        conn.execute("INSERT INTO conversations (conversation_id, backend_key, model, "
                     "workspace_folder, access, latest_sequence, created_at) "
                     "VALUES ('conv_gone', 'claude', 'opus', '/tmp/w', 'full', 3, 1700000000)")
        conn.executemany("INSERT INTO conversation_events (conversation_id, sequence, kind, "
                         "payload, created_at) VALUES ('conv_gone', ?, ?, ?, ?)",
                         [(s, k, json.dumps(p), 1700000000 + s) for s, k, p in events])
        conn.execute("INSERT INTO ticket_conversations (conversation_id, ticket_id) "
                     "VALUES ('conv_gone', ?)", (ticket_id,))
        conn.execute("UPDATE tickets SET conversation_id = 'conv_gone' WHERE id = ?", (ticket_id,))

    page = context_factory().new_page()
    page.goto(server.base + f"/#/workspace/{ticket_id}")
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page.wait_for_timeout(3000)

    print("\nA missing picture and a missing video, before anybody presses anything:")
    print("  quiet lines on screen:",
          [page.locator(".quiet-line").nth(i).inner_text()
           for i in range(page.locator(".quiet-line").count())][:6])
    print("  video elements       :", page.locator("video").count())
    print("  video previews       :", page.locator('[data-file-preview-kind="video"]').count())
    name = page.locator('[data-file-preview-kind="video"] .file-preview-name-link')
    print("  the video names itself:", name.count() and name.first.inner_text())

    video = page.locator("video").first
    if video.count():
        video.evaluate("v => { v.muted = true; return v.play().catch(() => {}); }")
        page.wait_for_timeout(2500)
        print("\nafter pressing play on the missing video:")
        print("  quiet lines:",
              [page.locator(".quiet-line").nth(i).inner_text()
               for i in range(page.locator(".quiet-line").count())][:6])
        print("  video elements still:", page.locator("video").count())
