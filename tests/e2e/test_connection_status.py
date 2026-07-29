"""Browser proof for what the quiet shell status says and for catching up.

The connection label has two live states: connected or reconnecting. Blocking the change
stream puts it in the second state; letting the stream through puts it back in the
first. What matters most is the catch-up: everything that changed while the browser
could not hear anything is on the screen once the stream is back, with no reload.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

from planner.environments.deployment_lifecycle import DeploymentLifecycleStore

WAIT_MS = 10_000
# The browser retries a dropped stream on its own schedule, so reconnection waits are
# given a longer budget than a same-page assertion needs.
RECONNECT_WAIT_MS = 30_000


def _status(page: Page) -> str | None:
    return page.locator("[data-connection-status]").get_attribute("data-state")


def _set_running_worker(server: ServerHandle, ticket_id: str) -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute("UPDATE tickets SET ticket_status = 'agent' WHERE id = ?", (ticket_id,))


def _deployment_store(server: ServerHandle) -> DeploymentLifecycleStore:
    return DeploymentLifecycleStore(
        Path(server.db_path).parent / "deployment-lifecycle.json"
    )


def _assert_desktop_shell_signals_have_geometry(page: Page) -> None:
    presence = page.locator("[data-shell-presence]")
    status = page.locator(".shell-connection")
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

    # The stream cannot open, and the shell status says so.
    page.locator('[data-connection-status][data-state="reconnecting"]').wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert page.locator(".shell-connection").inner_text() == "Reconnecting"
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
    assert page.locator(".shell-connection").inner_text() == "Connected"
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

    # Worker presence remains beside connection state at both widths, outside the
    # navigation itself.
    _assert_desktop_shell_signals_have_geometry(page)
    page.set_viewport_size({"width": 390, "height": 720})
    page.locator("[data-shell-presence]", has_text="1 working").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.locator("[data-connection-status]").wait_for(state="visible", timeout=WAIT_MS)


def test_lifecycle_file_change_updates_semantic_status_while_stream_stays_connected(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    page.goto(server.base + "/#/day")
    status = page.locator("[data-connection-status]")
    page.locator('[data-connection-state="connected"]').wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.evaluate("() => { window.__documentMark = 'same document'; }")

    _deployment_store(server).start(
        "e2e-connected-change",
        "a" * 40,
        prior_sha="b" * 40,
    )

    page.locator('[data-status-state="preparing"]', has_text="Preparing").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert status.get_attribute("data-connection-state") == "connected"
    assert page.evaluate("() => window.__documentMark") == "same document"


def test_planned_phase_survives_transport_loss_then_terminal_state_reconciles(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    store = _deployment_store(server)
    store.start(
        "e2e-restart-reconcile",
        "c" * 40,
        prior_sha="d" * 40,
    )

    attempts: list[str] = []
    blocked = True

    def change_stream(route: Route) -> None:
        attempts.append(route.request.url)
        if blocked:
            route.abort()
            return
        route.continue_()

    context = context_factory()
    page = context.new_page()
    context.route("**/api/changes", change_stream)
    page.goto(server.base + "/#/day")
    page.evaluate("() => { window.__documentMark = 'same document'; }")

    # Deployment evidence, not transport optimism, retains the planned phase.
    page.locator(
        '[data-connection-state="reconnecting"][data-status-state="preparing"]',
        has_text="Preparing",
    ).wait_for(state="visible", timeout=WAIT_MS)
    assert attempts

    store.transition(
        "e2e-restart-reconcile",
        "failed",
        detail="The replacement did not become healthy.",
        code="health_check_failed",
    )

    # A fresh document can still read the durable problem while its own stream is
    # unavailable. The transport drop cannot erase terminal deployment evidence.
    problem_page = context.new_page()
    problem_page.goto(server.base + "/#/day")
    problem_page.locator(
        '[data-connection-state="reconnecting"][data-status-state="problem"]',
        has_text="Problem",
    ).wait_for(state="visible", timeout=WAIT_MS)
    problem_page.close()

    blocked = False

    # EventSource reconnects in the same document. Opening the stream reconciles the
    # endpoint even if the filesystem notification itself was missed during the gap.
    page.locator(
        '[data-connection-state="connected"][data-status-state="problem"]',
        has_text="Problem",
    ).wait_for(state="visible", timeout=RECONNECT_WAIT_MS)
    assert page.evaluate("() => window.__documentMark") == "same document"
    assert len(attempts) >= 2


def test_shell_status_is_non_interactive_and_does_not_probe_vps_summary(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
) -> None:
    page = context_factory().new_page()
    summary_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            summary_requests.append(request.url)
            if request.url.endswith("/api/vps-status-summary")
            else None
        ),
    )
    page.goto(server.base + "/#/day")
    status = page.locator("[data-connection-status]")
    status.wait_for(state="visible", timeout=WAIT_MS)
    assert summary_requests == []
    assert status.locator("button, a, input, select").count() == 0
    assert status.get_attribute("role") == "status"


def test_real_process_restart_reconciles_terminal_lifecycle_in_the_same_document(
    server_factory: Callable[..., ServerHandle],
    stop_server: Callable[[ServerHandle], None],
    restart_server: Callable[[ServerHandle], ServerHandle],
    context_factory: Callable[[], BrowserContext],
) -> None:
    first_server = server_factory()
    store = _deployment_store(first_server)
    store.start(
        "e2e-real-restart",
        "e" * 40,
        prior_sha="f" * 40,
    )

    page = context_factory().new_page()
    page.goto(first_server.base + "/#/day")
    page.locator(
        '[data-connection-state="connected"][data-status-state="preparing"]',
        has_text="Preparing",
    ).wait_for(state="visible", timeout=WAIT_MS)

    store.transition("e2e-real-restart", "restarting")
    page.locator(
        '[data-connection-state="connected"][data-status-state="restarting"]',
        has_text="Restarting",
    ).wait_for(state="visible", timeout=WAIT_MS)
    page.evaluate("() => { window.__documentMark = 'same document'; }")
    url_before = page.url
    opens_before = page.evaluate("() => window.__plannerDebug.sseOpens")

    stop_server(first_server)
    page.locator(
        '[data-connection-state="reconnecting"][data-status-state="restarting"]',
        has_text="Restarting",
    ).wait_for(state="visible", timeout=WAIT_MS)

    # The runner remains able to write its durable account while no app process exists.
    store.transition(
        "e2e-real-restart",
        "failed",
        detail="The replacement process did not become healthy.",
        code="process_restart_failed",
    )
    replacement = restart_server(first_server)
    assert replacement.base == first_server.base
    assert replacement.db_path == first_server.db_path

    page.locator(
        '[data-connection-state="connected"][data-status-state="problem"]',
        has_text="Problem",
    ).wait_for(state="visible", timeout=RECONNECT_WAIT_MS)
    assert page.url == url_before
    assert page.evaluate("() => window.__documentMark") == "same document"
    page.wait_for_function(
        "(opens) => window.__plannerDebug.sseOpens > opens",
        arg=opens_before,
        timeout=RECONNECT_WAIT_MS,
    )

    # The replacement lifespan created a new file observer: a subsequent external
    # replacement is announced without another process restart or browser reload.
    store.start(
        "e2e-observer-after-restart",
        "1" * 40,
        expected_deployment_id="e2e-real-restart",
        prior_sha="f" * 40,
    )
    page.locator(
        '[data-connection-state="connected"][data-status-state="preparing"]',
        has_text="Preparing",
    ).wait_for(state="visible", timeout=WAIT_MS)
    assert page.evaluate("() => window.__documentMark") == "same document"
