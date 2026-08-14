"""A Ticket conversation's loopback link opens through the Panels ingress."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle

from planner.conversation.events import AgentMessageEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore


class _PreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler names the hook
        if self.path == "/demo/start?view=full":
            body = (
                b"<!doctype html><html><body>"
                b"<h1>Ticket branch preview</h1>"
                b'<a href="next">Open the next preview page</a>'
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/demo/next":
            body = b"<!doctype html><html><body><h1>Relative navigation works</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _preview_server() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PreviewHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=10)
        assert not worker.is_alive()


def _append_agent_message(server: ServerHandle, conversation_id: str, markdown: str) -> None:
    async def append() -> None:
        await ConversationStore(str(server.db_path)).append_event(
            conversation_id,
            AgentMessageEventPayload(content=text_message_content(markdown)),
        )

    failures: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(append())
        except BaseException as error:  # noqa: BLE001 - re-raised on the browser thread
            failures.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    if failures:
        raise failures[0]


def test_agent_loopback_link_opens_the_ticket_server_through_panels(
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
        "Open a Ticket dev server",
    )["id"]
    conversation_id = "conv-ticket-dev-server-link"
    started = httpx.post(
        f"{server.base}/api/conversation/conversations",
        json={
            "conversation_id": conversation_id,
            "backend_key": "codex",
            "model": "e2e-model",
        },
        timeout=10,
    )
    assert started.status_code == 201, started.text
    with sqlite3.connect(server.db_path) as connection:
        connection.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (conversation_id, ticket_id),
        )

    with _preview_server() as preview_port:
        _append_agent_message(
            server,
            conversation_id,
            f"[Open the Ticket branch preview](http://127.0.0.1:{preview_port}/demo/start?view=full#section)",
        )
        page = open_page(
            context_factory(),
            server,
            f"#/workspace/{ticket_id}",
            f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        )
        page.click("[data-conversation-input]", timeout=WAIT_MS)
        link = page.locator(
            '[data-conversation-row="agent_message"] a',
            has_text="Open the Ticket branch preview",
        )
        link.wait_for(timeout=WAIT_MS)
        expected_href = (
            f"/dev/tickets/{ticket_id}/{preview_port}/demo/start?view=full#section"
        )
        assert link.get_attribute("href") == expected_href

        link.click(timeout=WAIT_MS)
        page.wait_for_selector("h1", timeout=WAIT_MS)
        assert page.inner_text("h1") == "Ticket branch preview"
        assert page.url == server.base + expected_href

        page.click("text=Open the next preview page", timeout=WAIT_MS)
        page.wait_for_selector("h1", timeout=WAIT_MS)
        assert page.inner_text("h1") == "Relative navigation works"
        assert page.url == f"{server.base}/dev/tickets/{ticket_id}/{preview_port}/demo/next"
