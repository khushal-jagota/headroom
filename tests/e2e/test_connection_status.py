"""Browser proof for what the combined status trigger says and for catching up.

The VPS trigger has two live states: connected or reconnecting. Blocking the change
stream puts it in the second state; letting the stream through puts it back in the
first. What matters most is the catch-up: everything that changed while the browser
could not hear anything is on the screen once the stream is back, with no reload.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

WAIT_MS = 10_000
# The browser retries a dropped stream on its own schedule, so reconnection waits are
# given a longer budget than a same-page assertion needs.
RECONNECT_WAIT_MS = 30_000


def _status(page: Page) -> str | None:
    return page.locator("[data-connection-status]").get_attribute("data-state")


def _set_running_worker(server: ServerHandle, ticket_id: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute("UPDATE tickets SET ticket_status = 'agent' WHERE id = ?", (ticket_id,))


def _assert_desktop_shell_signals_have_geometry(page: Page) -> None:
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


def test_blocked_change_stream_says_reconnecting_and_catches_up_once_it_returns(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
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

    attempts: list[str] = []
    blocked = True

    def change_stream(route: Route) -> None:
        attempts.append(route.request.url)
        if blocked:
            route.abort()
            return
        route.continue_()

    page = context_factory().new_page()
    page.route("**/api/changes", change_stream)
    page.goto(server.base + f"/#/ticket/{ticket_id}")
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    page.evaluate("() => { window.__documentMark = 'same document'; }")
    url_before = page.url

    # The stream cannot open, and the combined VPS trigger says so.
    page.locator('[data-connection-status][data-state="reconnecting"]').wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert page.locator("[data-connection-status]").inner_text() == "Reconnecting"
    assert attempts, attempts
    assert page.evaluate("() => window.__plannerDebug.sseOpens") == 0

    # Two changes land while the browser is deaf to them: this ticket's title, and a
    # worker starting work — one on the ticket screen, one in the shell.
    api.direct_patch(server, f"/api/tickets/{ticket_id}", {"title": "Connection status after"})
    _set_running_worker(server, ticket_id)
    assert api.get(server, f"/api/tickets/{ticket_id}")["title"] == "Connection status after"
    assert page.locator(".ticket-title", has_text="Connection status after").count() == 0

    blocked = False

    # The browser retries by itself; the stream opens, the pill flips, and every screen
    # that is showing catches up on what it missed — without a reload.
    page.locator('[data-connection-status][data-state="connected"]').wait_for(
        state="visible", timeout=RECONNECT_WAIT_MS
    )
    assert _status(page) == "connected"
    assert page.locator("[data-connection-status]").inner_text() == "Connected"
    page.wait_for_function(
        "() => window.__plannerDebug.sseOpens >= 1", timeout=RECONNECT_WAIT_MS
    )
    page.locator(".ticket-title", has_text="Connection status after").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.locator("[data-shell-presence]", has_text="1 working").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert page.url == url_before
    assert page.evaluate("() => window.__documentMark") == "same document"
    assert len(attempts) >= 2, attempts

    # Worker presence remains beside the combined status on desktop and leaves the
    # constrained mobile navigation entirely.
    _assert_desktop_shell_signals_have_geometry(page)
    page.set_viewport_size({"width": 390, "height": 720})
    page.locator("[data-shell-presence]").wait_for(state="hidden", timeout=WAIT_MS)
    page.locator("[data-connection-status]").wait_for(state="visible", timeout=WAIT_MS)
