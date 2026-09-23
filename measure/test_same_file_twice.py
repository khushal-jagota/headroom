"""One file, named by two messages in the same view. How many times is it asked for?"""
from __future__ import annotations
import collections, json, sqlite3
from collections.abc import Callable
from playwright.sync_api import BrowserContext
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle
from measure.test_measure_duplicate_fetches import _seed_files

PERF = """
() => performance.getEntriesByType('resource')
  .filter(e => e.name.includes('/files/tickets/'))
  .map(e => e.name.split('/files/tickets/')[1])
"""


def test_same_file_named_by_three_messages(
    server: ServerHandle, context_factory: Callable[[], BrowserContext],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Same file twice")["id"]
    _seed_files(server, ticket_id)
    owner = {"kind": "owner", "id": "owner"}
    me = {"kind": "ticket", "id": ticket_id}
    one = f"/files/tickets/{ticket_id}/images/shot-0.png"
    note = f"/files/tickets/{ticket_id}/notes/note-0.md"
    events, seq = [], 0
    for turn in range(3):
        seq += 1
        events.append((seq, "prompt", {"text": f"Ask {turn}", "sender_label": "owner",
                                       "mode": "queue", "sender": owner, "recipient": me}))
        seq += 1
        events.append((seq, "message_to_owner", {
            "text": f"Answer {turn}\n\n![shot](%s)\n\n[note](%s)" % (one, note),
            "sender_label": "Coding worker", "sender": me, "recipient": owner}))
        seq += 1
        events.append((seq, "turn_ended", {"ending": "completed", "error_summary": None}))

    with sqlite3.connect(server.db_path) as conn:
        conn.execute("INSERT INTO conversations (conversation_id, backend_key, model, "
                     "workspace_folder, access, latest_sequence, created_at) "
                     "VALUES ('conv_same', 'claude', 'opus', '/tmp/w', 'full', ?, ?)",
                     (seq, 1_700_000_000))
        conn.executemany("INSERT INTO conversation_events (conversation_id, sequence, kind, "
                         "payload, created_at) VALUES ('conv_same', ?, ?, ?, ?)",
                         [(s, k, json.dumps(p), 1_700_000_000 + s) for s, k, p in events])
        conn.execute("INSERT INTO ticket_conversations (conversation_id, ticket_id) "
                     "VALUES ('conv_same', ?)", (ticket_id,))
        conn.execute("UPDATE tickets SET conversation_id = 'conv_same' WHERE id = ?", (ticket_id,))

    page = context_factory().new_page()
    network: list[str] = []
    # no interception: this is what a default browser really does
    page.on("request", lambda r: network.append(r.url.split("/files/tickets/")[1])
            if "/files/tickets/" in r.url else None)
    page.goto(server.base + f"/#/workspace/{ticket_id}")
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page.wait_for_timeout(4000)
    asked = collections.Counter(page.evaluate(PERF))
    print("\nThree messages each name the same two files. Distinct files on screen: 2")
    print("  previews mounted:", page.locator("[data-file-preview]").count())
    print("  asked (resource timeline):", dict(asked))
    print("  reached the network       :", dict(collections.Counter(network)))
