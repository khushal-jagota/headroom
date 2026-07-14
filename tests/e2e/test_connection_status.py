"""Browser proof for event-stream liveness and recovery reconciliation."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable

from playwright.sync_api import Page, WebSocketRoute

WAIT_MS = 10_000


def _wait_for(
    page: Page,
    description: str,
    predicate: Callable[[], bool],
    timeout_ms: int = WAIT_MS,
) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"timed out waiting for {description}")


def _latest_event(server, entity_id: str) -> dict:
    with sqlite3.connect(server.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, entity_id, kind, payload, created_at FROM events "
            "WHERE entity_id = ? ORDER BY id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
    assert row is not None
    return {
        "id": row["id"],
        "entity_id": row["entity_id"],
        "kind": row["kind"],
        "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
    }


def _event_cursor(server) -> int:
    with sqlite3.connect(server.db_path) as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
    return int(row[0])


def _set_running_worker_without_event(server, ticket_id: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status = 'agent_running_step' WHERE id = ?",
            (ticket_id,),
        )


def _send(socket: WebSocketRoute, payload: dict) -> None:
    socket.send(json.dumps(payload))


def _finish_mock_close(socket: WebSocketRoute) -> None:
    socket.close(code=1001, reason="mock liveness timeout")


def _status_text(page: Page) -> str:
    return page.locator("[data-connection-status]").inner_text(timeout=WAIT_MS).strip()


def _assert_shell_signals_have_geometry(page: Page) -> None:
    presence = page.locator("[data-shell-presence]")
    status = page.locator("[data-connection-status]")
    presence.wait_for(state="visible", timeout=WAIT_MS)
    status.wait_for(state="visible", timeout=WAIT_MS)
    presence_box = presence.bounding_box()
    status_box = status.bounding_box()
    assert presence_box is not None
    assert status_box is not None
    separated = (
        presence_box["x"] + presence_box["width"] <= status_box["x"]
        or status_box["x"] + status_box["width"] <= presence_box["x"]
        or presence_box["y"] + presence_box["height"] <= status_box["y"]
        or status_box["y"] + status_box["height"] <= presence_box["y"]
    )
    assert separated, {"presence": presence_box, "status": status_box}


def test_connection_status_recovers_with_cursor_replay_and_subscribed_reconciliation(
    server, context_factory, cli, api
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Connection status before",
    )["id"]
    baseline_cursor = _event_cursor(server)
    baseline_event = _latest_event(server, ticket_id)

    sockets: list[WebSocketRoute] = []
    socket_urls: list[str] = []
    requests: list[tuple[str, str]] = []
    connect_retries = False
    page = context_factory().new_page()

    def capture_socket(socket: WebSocketRoute) -> None:
        socket_urls.append(socket.url)
        sockets.append(socket)
        if connect_retries:
            socket.connect_to_server()

    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.route_web_socket("**/api/events*", capture_socket)

    page.goto(server.base + f"/#/ticket/{ticket_id}")
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        timeout=WAIT_MS,
    )
    page.evaluate(
        """
        () => {
          const screen = document.querySelector('section[data-screen="ticket"]');
          const status = document.querySelector('[data-connection-status]');
          window.__connectionStatusProbe = {
            navigationStarts: performance.getEntriesByType('navigation').length,
            states: [status.dataset.state]
          };
          new MutationObserver(() => {
            window.__connectionStatusProbe.states.push(status.dataset.state);
          }).observe(status, { attributes: true, attributeFilter: ['data-state'] });
          screen.dataset.connectionProbe = 'stable';
        }
        """
    )

    _wait_for(page, "initial routed event socket", lambda: len(sockets) == 1)
    assert socket_urls[0].endswith("/api/events?since=0")
    assert _status_text(page) == "Reconnecting"
    _send(sockets[0], {"events": [baseline_event], "cursor": baseline_cursor})
    page.locator('[data-connection-status][data-state="connected"]').wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert _status_text(page) == "Connected"
    page.wait_for_function(
        "() => window.__plannerDebug.flushes >= 1",
        timeout=WAIT_MS,
    )
    flushes_after_event = page.evaluate("() => window.__plannerDebug.flushes")
    _send(sockets[0], {"events": [], "cursor": baseline_cursor})
    page.wait_for_timeout(75)
    assert page.evaluate("() => window.__plannerDebug.flushes") == flushes_after_event

    api.direct_patch(server, f"/api/tickets/{ticket_id}", {"title": "Connection status after"})
    _set_running_worker_without_event(server, ticket_id)
    requests.clear()
    connect_retries = True

    page.locator('[data-connection-status][data-state="reconnecting"]').wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert _status_text(page) == "Reconnecting"

    _wait_for(page, "retry after liveness timeout", lambda: len(sockets) >= 2)
    assert socket_urls[-1].endswith(f"/api/events?since={baseline_cursor}")

    page.locator('[data-connection-status][data-state="connected"]').wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.locator(".ticket-title", has_text="Connection status after").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.locator("[data-shell-presence]", has_text="1 working").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.wait_for_function(
        "ticketKey => window.__plannerDebug.invalidations[ticketKey] >= 1",
        arg=f"ticket:{ticket_id}",
        timeout=WAIT_MS,
    )
    _finish_mock_close(sockets[0])
    page.wait_for_timeout(600)
    assert _status_text(page) == "Connected"
    assert len(sockets) == 2
    assert ("GET", server.base + "/api/review") in requests
    assert page.evaluate(
        """
        () => {
          const screen = document.querySelector('section[data-screen="ticket"]');
          return window.__connectionStatusProbe.navigationStarts ===
            performance.getEntriesByType('navigation').length
            && screen?.dataset.connectionProbe === 'stable';
        }
        """
    )
    observed_states = page.evaluate("() => window.__connectionStatusProbe.states")
    deduplicated_states = [
        state
        for index, state in enumerate(observed_states)
        if index == 0 or state != observed_states[index - 1]
    ]
    assert deduplicated_states == [
        "reconnecting",
        "connected",
        "reconnecting",
        "offline",
        "connected",
    ]

    _assert_shell_signals_have_geometry(page)
    page.set_viewport_size({"width": 390, "height": 720})
    _assert_shell_signals_have_geometry(page)
