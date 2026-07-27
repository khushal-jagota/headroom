"""Route-level geometry and responsive visibility for every chat host.

Every host is on the conversation system's own pane now. On a Ticket that pane is the
SECTION UNDER the ticket, sharing the page's height with it; on the Chief's desk it fills
its container. The geometry contract is that the pane fits its host, that the section is
placed and sized against the ticket screen rather than the window — the same route also
renders inside the Workspace's right pane, where those two are nothing like the same
rectangle — and that each host appears at the width where it is meant to.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Browser, Page
from tests.e2e.harness import ApiHelper, ServerHandle

WAIT_MS = 10_000

CONVERSATION_PANE = "[data-conversation-pane]"
CONVERSATION_THREAD = "[data-conversation-thread]"
CONVERSATION_LAYER = "[data-conversation-layer-host]"
CONVERSATION_INPUT = "[data-conversation-input]"


def _open_route(
    browser: Browser,
    base_url: str,
    route: str,
    ready_selector: str,
    width: int,
) -> Page:
    page = browser.new_page(viewport={"width": width, "height": 720})
    page.goto(base_url + "/" + route)
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    return page


def _width_geometry(
    page: Page, host_selector: str, pane_selector: str, thread_selector: str
) -> dict[str, Any]:
    geometry: dict[str, Any] = page.evaluate(
        """([hostSelector, paneSelector, threadSelector]) => {
          const tolerance = 1;
          const host = document.querySelector(hostSelector);
          const pane = host.querySelector(paneSelector);
          const thread = pane.querySelector(threadSelector);
          const inside = (child, parent) => {
            const childRect = child.getBoundingClientRect();
            // A box that is not drawn has no width to overflow with. The thread is still
            // mounted at rest and simply gives up its height, so this is the ordinary
            // case rather than a hole in the check.
            if (childRect.width === 0 && childRect.height === 0) return true;
            const parentRect = parent.getBoundingClientRect();
            return childRect.left >= parentRect.left - tolerance
              && childRect.right <= parentRect.right + tolerance;
          };
          return {
            documentClient: document.documentElement.clientWidth,
            documentScroll: document.documentElement.scrollWidth,
            bodyClient: document.body.clientWidth,
            bodyScroll: document.body.scrollWidth,
            hostClient: host.clientWidth,
            hostScroll: host.scrollWidth,
            paneClient: pane.clientWidth,
            paneScroll: pane.scrollWidth,
            threadClient: thread.clientWidth,
            threadScroll: thread.scrollWidth,
            paneInsideHost: inside(pane, host),
            threadInsidePane: inside(thread, pane),
          };
        }""",
        [host_selector, pane_selector, thread_selector],
    )
    return geometry


def _assert_bounded(
    page: Page,
    host_selector: str,
    pane_selector: str = CONVERSATION_PANE,
    thread_selector: str = CONVERSATION_THREAD,
) -> None:
    geometry = _width_geometry(page, host_selector, pane_selector, thread_selector)
    assert geometry["documentScroll"] <= geometry["documentClient"], geometry
    assert geometry["bodyScroll"] <= geometry["bodyClient"], geometry
    assert geometry["hostScroll"] <= geometry["hostClient"], geometry
    assert geometry["paneScroll"] <= geometry["paneClient"], geometry
    assert geometry["threadScroll"] <= geometry["threadClient"], geometry
    assert geometry["paneInsideHost"], geometry
    assert geometry["threadInsidePane"], geometry


def _placement(page: Page, screen_selector: str) -> dict[str, Any]:
    """Where the conversation sits, beside the ticket screen it is supposed to be placed
    against and beside the window it must not be placed against.

    The page's own bottom gap comes back with it, because that gap is what the conversation
    stops short of the screen by and it is written in one place — the stylesheet.
    """
    placement: dict[str, Any] = page.evaluate(
        """([screenSelector, layerSelector]) => {
          const screen = document.querySelector(screenSelector);
          const layer = screen.querySelector(layerSelector);
          const page = screen.querySelector('.ticket-page');
          const screenRect = screen.getBoundingClientRect();
          const layerRect = layer.getBoundingClientRect();
          return {
            screenLeft: screenRect.left,
            screenRight: screenRect.right,
            screenTop: screenRect.top,
            screenBottom: screenRect.bottom,
            screenHeight: screenRect.height,
            layerLeft: layerRect.left,
            layerRight: layerRect.right,
            layerTop: layerRect.top,
            layerBottom: layerRect.bottom,
            layerHeight: layerRect.height,
            pageGap: parseFloat(getComputedStyle(page).paddingBottom),
            windowWidth: window.innerWidth,
            windowHeight: window.innerHeight,
          };
        }""",
        [screen_selector, CONVERSATION_LAYER],
    )
    return placement


def _assert_the_conversation_ends_the_ticket_screen(
    page: Page, screen_selector: str
) -> dict[str, Any]:
    """The conversation is the last thing on the ticket page: its full width, and its
    bottom one page-gap above the bottom of the screen — the same gap the nav leaves above
    the ticket's title."""
    placement = _placement(page, screen_selector)
    tolerance = 1
    assert placement["pageGap"] > 0, placement
    assert abs(placement["layerLeft"] - placement["screenLeft"]) <= tolerance, placement
    assert abs(placement["layerRight"] - placement["screenRight"]) <= tolerance, placement
    assert (
        abs(placement["layerBottom"] - (placement["screenBottom"] - placement["pageGap"]))
        <= tolerance
    ), placement
    assert placement["layerTop"] >= placement["screenTop"] - tolerance, placement
    return placement


def test_the_ticket_conversation_is_a_section_measured_against_the_ticket_screen(
    browser: Browser,
    server: ServerHandle,
    api: ApiHelper,
) -> None:
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {"worker_type": "coding", "title": "Chat width route fixture"},
    )
    ticket_id = ticket["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    ticket_screen = f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]'

    # A Ticket on its own page. Here the ticket screen is the whole area below the nav,
    # so the conversation spans the window's width — and takes little of its height,
    # because at rest it is only the composer and the one line above it.
    page = _open_route(
        browser,
        server.base,
        f"#/ticket/{ticket_id}",
        f"{ticket_screen} {CONVERSATION_LAYER}",
        961,
    )
    try:
        assert page.locator(CONVERSATION_LAYER).is_visible()
        _assert_bounded(page, CONVERSATION_LAYER)
        rest = _assert_the_conversation_ends_the_ticket_screen(page, ticket_screen)
        assert rest["layerHeight"] < rest["screenHeight"] / 2, rest

        # The two of them share the page and neither is over the other, so the ticket
        # scrolls to an end that stops where the conversation starts. Nothing is reserved
        # and nothing has to be kept in step: the ticket simply gets what is left.
        assert page.evaluate(
            """() => {
              const doc = document.querySelector('.ticket-doc');
              const layer = document.querySelector('[data-conversation-layer-host]');
              doc.scrollTop = doc.scrollHeight;
              const last = doc.querySelector('.fields').getBoundingClientRect();
              return last.bottom <= layer.getBoundingClientRect().top + 1;
            }"""
        ), "the ticket's last content must stop above the conversation at rest"

        # Peeked: the conversation takes a bit over half the ticket screen and the ticket
        # keeps the rest, with the transcript drawn and fitting.
        page.click(CONVERSATION_INPUT)
        page.wait_for_selector(
            f'{CONVERSATION_PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS
        )
        _assert_bounded(page, CONVERSATION_LAYER)
        peeked = _assert_the_conversation_ends_the_ticket_screen(page, ticket_screen)
        assert peeked["screenHeight"] / 2 < peeked["layerHeight"] < peeked["screenHeight"], peeked

        # A section along the bottom of a single-column page has nothing to give up on a
        # narrow screen, so the conversation stays. A rail could not: it was a second
        # column, and below 961 there was no room for one.
        page.set_viewport_size({"width": 960, "height": 720})
        assert page.locator(CONVERSATION_LAYER).is_visible()
        _assert_bounded(page, CONVERSATION_LAYER)
        _assert_the_conversation_ends_the_ticket_screen(page, ticket_screen)
    finally:
        page.close()

    # The same route inside the Workspace's right pane. This is the case that proves the
    # conversation is sized against the ticket screen and NOT the window: the ticket
    # screen starts well inside the window here, and the conversation starts with it.
    workspace = _open_route(
        browser,
        server.base,
        f"#/workspace/{ticket_id}",
        f'[data-screen="workspace"] {ticket_screen} {CONVERSATION_LAYER}',
        1200,
    )
    try:
        assert workspace.locator(CONVERSATION_LAYER).is_visible()
        _assert_bounded(workspace, CONVERSATION_LAYER)
        inside_pane = _assert_the_conversation_ends_the_ticket_screen(workspace, ticket_screen)
        assert inside_pane["layerLeft"] > 0, inside_pane
        assert inside_pane["layerRight"] - inside_pane["layerLeft"] < inside_pane["windowWidth"], (
            inside_pane
        )

        # The Workspace still drops its whole right pane below 961, and the Ticket in it —
        # conversation and all — goes with it. That is the Workspace's rule, not this one's.
        workspace.set_viewport_size({"width": 960, "height": 720})
        assert not workspace.locator(".board-workspace-right").is_visible()
        assert not workspace.locator(CONVERSATION_LAYER).is_visible()
    finally:
        workspace.close()

    # The Workspace desk showing the Chief is not a layer, and it disappears below 961
    # rather than squeezing.
    desk = _open_route(
        browser,
        server.base,
        "#/workspace",
        '[data-screen="workspace"] .board-workspace-desk-inner',
        961,
    )
    try:
        assert desk.locator(".board-workspace-desk-inner").is_visible()
        _assert_bounded(desk, ".board-workspace-desk-inner")
        desk.set_viewport_size({"width": 960, "height": 720})
        assert not desk.locator(".board-workspace-desk-inner").is_visible()
    finally:
        desk.close()

    chief = _open_route(
        browser,
        server.base,
        "#/chief",
        f"[data-chief-of-staff-route] {CONVERSATION_PANE}",
        961,
    )
    try:
        assert chief.locator(".chief-chat-shell").is_visible()
        _assert_bounded(chief, ".chief-chat-shell")
        chief.set_viewport_size({"width": 390, "height": 720})
        assert chief.locator(".chief-chat-shell").is_visible()
        _assert_bounded(chief, ".chief-chat-shell")
    finally:
        chief.close()
