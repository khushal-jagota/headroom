"""Route-level geometry and responsive visibility for every ACP chat host."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Browser, Page

WAIT_MS = 10_000


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


def _width_geometry(page: Page, host_selector: str) -> dict[str, Any]:
    return page.evaluate(
        """hostSelector => {
          const tolerance = 1;
          const host = document.querySelector(hostSelector);
          const pane = host.querySelector('[data-acp-conversation-pane]');
          const thread = pane.querySelector('[data-chat-messages]');
          const inside = (child, parent) => {
            const childRect = child.getBoundingClientRect();
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
        host_selector,
    )


def _assert_bounded(page: Page, host_selector: str) -> None:
    geometry = _width_geometry(page, host_selector)
    assert geometry["documentScroll"] <= geometry["documentClient"], geometry
    assert geometry["bodyScroll"] <= geometry["bodyClient"], geometry
    assert geometry["hostScroll"] <= geometry["hostClient"], geometry
    assert geometry["paneScroll"] <= geometry["paneClient"], geometry
    assert geometry["threadScroll"] <= geometry["threadClient"], geometry
    assert geometry["paneInsideHost"], geometry
    assert geometry["threadInsidePane"], geometry


def test_chat_hosts_own_width_and_follow_the_960px_visibility_contract(
    browser: Browser,
    server: Any,
    api: Any,
) -> None:
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {"worker_type": "coding", "title": "Chat width route fixture"},
    )
    ticket_id = ticket["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    route_cases = [
        (
            f"#/ticket/{ticket_id}",
            f'[data-screen="ticket"][data-ticket-id="{ticket_id}"] .chat-rail',
            ".chat-rail",
        ),
        (
            f"#/workspace/{ticket_id}",
            f'[data-screen="workspace"] [data-screen="ticket"][data-ticket-id="{ticket_id}"]',
            ".board-workspace-right .chat-rail",
        ),
        (
            "#/workspace",
            '[data-screen="workspace"] .board-workspace-desk-inner',
            ".board-workspace-desk-inner",
        ),
    ]

    for route, ready_selector, host_selector in route_cases:
        page = _open_route(browser, server.base, route, ready_selector, 961)
        try:
            assert page.locator(host_selector).is_visible()
            _assert_bounded(page, host_selector)
            page.set_viewport_size({"width": 960, "height": 720})
            assert not page.locator(host_selector).is_visible()
        finally:
            page.close()

    chief = _open_route(
        browser,
        server.base,
        "#/chief",
        "[data-chief-of-staff-route] [data-acp-conversation-pane]",
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
