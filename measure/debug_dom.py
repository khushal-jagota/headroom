from __future__ import annotations
import sqlite3
from collections.abc import Callable
import httpx
from playwright.sync_api import BrowserContext
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle
from measure.test_measure_duplicate_fetches import _seed_files, _seed_conversation


def test_debug(server: ServerHandle, context_factory: Callable[[], BrowserContext],
               cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Overfetch debug")["id"]
    paths = _seed_files(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_conversation(conn, "conv_debug", ticket_id, paths)

    ev = httpx.get(server.base + "/api/conversation/conversations/conv_debug/events?after=0",
                   timeout=10)
    print("\nEVENTS", ev.status_code, ev.text[:900])
    vw = httpx.get(server.base + "/api/conversation/conversations/conv_debug", timeout=10)
    print("\nVIEW", vw.status_code, vw.text[:500])

    page = context_factory().new_page()
    page.on("console", lambda m: print("CONSOLE:", m.type, m.text[:300]))
    page.on("pageerror", lambda e: print("PAGEERROR:", str(e)[:400]))
    page.goto(server.base + f"/#/workspace/{ticket_id}")
    page.wait_for_selector(f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS)
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page.wait_for_timeout(3000)
    print("\nrows:", page.locator("[data-conversation-row]").count(),
          "previews:", page.locator("[data-file-preview]").count())
    print("pane html head:", page.inner_html("[data-conversation-pane]")[:1500])


def test_log(server: ServerHandle, cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "log probe")["id"]
    paths = _seed_files(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_conversation(conn, "conv_log", ticket_id, paths)
    httpx.get(server.base + "/api/conversation/conversations/conv_log/events?after=0", timeout=10)
    print("\n--- server log tail ---")
    print("\n".join(server.log_path.read_text(errors="replace").splitlines()[-40:]))
