"""A person answering a Ticket's worker, in the browser where they answer it.

A Ticket parked on a filed proposal is waiting for its owner. Replying to the worker is
an answer of a kind — the proposal is being discussed rather than approved — so the
Ticket moves to paired. The ticket screen is the one place that knows both halves, and it
says a reply happened only once the conversation has taken the message.

No agent is involved and none is needed. The send is held inside the page and answered
with whatever fate this test chooses, so nothing is ever spawned. Everything else is real
and reaches the real server, including the call that moves the Ticket.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import BrowserContext, Page, Request
from tests.e2e.harness import REPO_ROOT, WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_dev_conversation_pane import _A_RED_PNG, HOLD_THE_SEND

from planner.conversation.contracts import ConversationBackendKey
from planner.core import server as server_module
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data

TICKET_SCREEN = '[data-screen="ticket"]'
COMPOSER = f"{TICKET_SCREEN} [data-conversation-input]"
SEND = f"{TICKET_SCREEN} [data-conversation-send]"
FATE = f"{TICKET_SCREEN} [data-conversation-fate]"
PROPOSAL = "# Success criteria\n\nThe suite goes green.\n"
REFUSED_TEXT = "did this reach anything"


class _AcceptingBackendChild:
    """The real conversation core's deterministic sink for this browser round trip."""

    async def start(self, _resolved_start: object, *, vendor_session_cursor: str | None) -> None:
        del vendor_session_cursor

    async def write_prompt(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def steer(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def cancel_running_turn(self) -> None:
        return None

    async def answer_permission_ask(self, _ask_id: str, _option_id: str) -> None:
        return None

    async def stop(self) -> None:
        return None


def _accepting_backend_factory(**_services: object) -> _AcceptingBackendChild:
    return _AcceptingBackendChild()


@contextmanager
def _browser_server_with_accepting_backend(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """A real HTTP/UI server whose backend sink is deterministic and in-process."""
    db_path = tmp_path / "planning.db"
    with connect(str(db_path)) as connection:
        create_schema(connection)
        ticket = tickets_data.create_ticket(
            connection,
            worker_type="coding",
            title="Send the worker pictures",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        connection.commit()

    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes-home"),
            "PLAN_DISPATCH_ENABLED": "0",
            "PLAN_SHUTDOWN_GRACE_SECONDS": "2",
        },
    )
    original_web_dist = server_module._WEB_DIST
    original_web_index = server_module._WEB_INDEX
    original_assets = server_module._ASSETS_DIR
    original_static = server_module._STATIC_DIR
    original_backend_factories = server_module.production_backend_child_factories
    server_module._WEB_DIST = REPO_ROOT / "web" / "dist"
    server_module._WEB_INDEX = server_module._WEB_DIST / "index.html"
    server_module._ASSETS_DIR = REPO_ROOT / "assets"
    server_module._STATIC_DIR = REPO_ROOT / "static"
    server_module.production_backend_child_factories = lambda **_machine: {
        key: _accepting_backend_factory for key in ConversationBackendKey
    }
    app = server_module.create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
    )
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = int(available.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", ticket.id
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        server_module._WEB_DIST = original_web_dist
        server_module._WEB_INDEX = original_web_index
        server_module._ASSETS_DIR = original_assets
        server_module._STATIC_DIR = original_static
        server_module.production_backend_child_factories = original_backend_factories
        assert not thread.is_alive()


def _parked_on_a_proposal(server: ServerHandle, cli: Callable[..., JsonObject]) -> str:
    """A Ticket its worker has filed a proposal on, waiting for its owner to answer."""
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Answer the worker",
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success criteria proposed.",
        ticket_id=ticket_id,
        stdin=PROPOSAL,
    )
    return ticket_id


def test_a_reply_in_the_pane_pairs_the_ticket_and_a_refusal_leaves_it_parked(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = _parked_on_a_proposal(server, cli)
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_approval"
    # Nothing is seeded: a Ticket nobody has spoken to has no conversation, and the first
    # message is what makes one.

    context = context_factory()
    context.add_init_script(HOLD_THE_SEND)
    page = open_page(
        context,
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )

    replies: list[str] = []

    def note_reply(request: Request) -> None:
        if request.method == "POST" and request.url.endswith("/human-reply"):
            replies.append(request.url)

    page.on("request", note_reply)
    page.wait_for_selector(f"{COMPOSER}:not([disabled])", timeout=WAIT_MS)

    # First, a message the conversation turns away. The words coming back to the person
    # who wrote them is the pane saying this send is over and got nowhere — so anything
    # it was going to do afterwards it has already not done.
    page.fill(COMPOSER, REFUSED_TEXT, timeout=WAIT_MS)
    page.click(SEND, timeout=WAIT_MS)
    page.wait_for_function("() => window.__heldSends.length === 1", timeout=WAIT_MS)
    # The owner's door answers with the fate and the conversation it happened in. This
    # message was to make one and did not land, so there is no conversation to name.
    page.evaluate(
        "() => window.__heldSends[0].answer("
        "{ conversation_id: null, fate: 'refused', refusal_reason: 'backend_did_not_start' })"
    )
    page.wait_for_function(
        "([selector, text]) => document.querySelector(selector).value === text",
        arg=[COMPOSER, REFUSED_TEXT],
        timeout=WAIT_MS,
    )
    page.wait_for_selector(FATE, timeout=WAIT_MS)
    assert "not delivered" in page.inner_text(FATE)
    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "awaiting_approval"

    # Now one the conversation holds for a busy agent. Held is reached, so this one is a
    # reply, and the screen says so as soon as the send comes back.
    page.fill(COMPOSER, "here is what I think of that", timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith(f"/api/tickets/{ticket_id}/human-reply")
        and response.status < 300,
        timeout=WAIT_MS,
    ):
        page.click(SEND, timeout=WAIT_MS)
        page.wait_for_function("() => window.__heldSends.length === 2", timeout=WAIT_MS)
        page.evaluate(
            "() => window.__heldSends[1].answer("
            "{ conversation_id: 'conv_made_by_the_message', fate: 'queued', queue_position: 1 })"
        )

    assert api.get(server, f"/api/tickets/{ticket_id}")["ticket_status"] == "paired"
    # One reply, from the send that got somewhere. The refused send is in front of it in
    # this page's own order, so a reply it had made would be counted here too.
    assert replies == [f"{server.base}/api/tickets/{ticket_id}/human-reply"]


def test_ticket_images_cross_the_owner_api_become_managed_files_and_reload(
    tmp_path: Path,
    context_factory: Callable[[], BrowserContext],
) -> None:
    """The shared Ticket surface through the real owner door, record and file route."""
    with _browser_server_with_accepting_backend(tmp_path) as (base, ticket_id):
        context = context_factory()
        page = context.new_page()
        page.goto(f"{base}/#/ticket/{ticket_id}")
        composer = f'{TICKET_SCREEN}[data-ticket-id="{ticket_id}"]'
        page.wait_for_selector(
            f"{composer} [data-conversation-input]:not([disabled])",
            timeout=30_000,
        )
        page.set_input_files(
            f"{composer} [data-conversation-image-input]",
            [
                {"name": "first.png", "mimeType": "image/png", "buffer": _A_RED_PNG},
                {"name": "second.png", "mimeType": "image/png", "buffer": _A_RED_PNG},
            ],
        )
        page.wait_for_function(
            "() => document.querySelectorAll('[data-chat-image-preview]').length === 2"
        )
        page.fill(f"{composer} [data-conversation-input]", "look at both")
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.endswith(f"/api/tickets/{ticket_id}/conversation/send")
            and response.status == 200,
            timeout=WAIT_MS,
        ) as sent:
            page.click(f"{composer} [data-conversation-send]")
        delivered = sent.value.json()
        assert delivered["fate"] == "started"
        conversation_id = delivered["conversation_id"]

        events = httpx.get(
            f"{base}/api/conversation/conversations/{conversation_id}/events",
            params={"after": 0},
            timeout=10,
        )
        assert events.status_code == 200, events.text
        prompt = next(event for event in events.json()["events"] if event["kind"] == "prompt")
        content = prompt["payload"]["content"]
        assert [piece["piece"] for piece in content] == ["text", "image", "image"]
        assert [piece.get("file_name") for piece in content[1:]] == [
            "first.png",
            "second.png",
        ]
        assert all("data" not in piece for piece in content[1:])
        for piece in content[1:]:
            kept = httpx.get(
                f"{base}/api/conversation/conversations/{conversation_id}/files/"
                f"{piece['stored_file_id']}",
                timeout=10,
            )
            assert kept.status_code == 200
            assert kept.content == _A_RED_PNG

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "() => {"
            "  const images = [...document.querySelectorAll("
            "    '[data-conversation-row=\"prompt\"] [data-conversation-piece=\"image\"]')];"
            "  return images.length === 2"
            "    && images.every((image) => image.complete && image.naturalWidth === 8);"
            "}",
            timeout=WAIT_MS,
        )
        page.close()
