"""Responsive shell navigation and compatibility routes."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ServerHandle


def test_desktop_more_reaches_grouped_destinations_and_marks_secondary_route(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('[data-screen="workspace"]', timeout=WAIT_MS)

    direct = page.locator(".shell-links > .nav-link")
    assert direct.count() == 3
    assert direct.nth(0).inner_text().startswith("Review")
    assert direct.nth(1).inner_text() == "Workspace"
    assert direct.nth(2).inner_text() == "Agents"

    more = page.locator('[data-screen="more"]')
    assert more.get_attribute("aria-controls") == "shell-more-panel"
    assert more.get_attribute("aria-haspopup") is None
    more.click()
    menu = page.locator("[data-shell-more-menu]")
    assert menu.is_visible()
    assert menu.get_attribute("id") == "shell-more-panel"
    assert menu.get_attribute("role") is None
    assert menu.locator('[role="menuitem"]').count() == 0
    assert menu.locator(".shell-more-group").all_inner_texts() == ["PLANNING", "SYSTEM"]
    assert menu.locator("a").all_inner_texts() == [
        "Day",
        "Sprint",
        "Backlog",
        "Ideas",
        "Config",
        "Backends",
        "Notifications",
    ]

    menu.locator('a[href="#/config"]').click()
    page.wait_for_selector('[data-screen="config"]', timeout=WAIT_MS)
    assert "active" in (more.get_attribute("class") or "").split()

    # Reselecting the current secondary route does not emit hashchange, so each link
    # closes the disclosure itself.
    more.click()
    page.locator('[data-shell-more-menu] a[href="#/config"]').click()
    assert not page.locator("[data-shell-more-menu]").is_visible()

    more.click()
    page.keyboard.press("Escape")
    assert not page.locator("[data-shell-more-menu]").is_visible()
    assert more.evaluate("(node) => node === document.activeElement")


def test_every_more_destination_has_its_canonical_screen(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    # Navigation owns reachability, not live provider discovery. Keep this route entirely
    # browser-fixtured so visiting Backends cannot probe a developer's installed tools.
    page.route(
        "**/api/conversation/backends**",
        lambda route: route.fulfill(json={"backends": []}),
    )
    destinations = [
        ("#/day", '[data-screen="day"]'),
        ("#/sprint", '[data-screen="sprint"]'),
        ("#/backlog", '[data-screen="backlog"]'),
        ("#/ideas", '[data-screen="ideas"]'),
        ("#/config", '[data-screen="config"] [data-workers-list]'),
        ("#/backends", '[data-screen="backends"]'),
        ("#/notifications", '[data-screen="notifications"]'),
    ]

    page.goto(server.base + "/#/workspace")
    page.locator('[data-screen="more"]').click()
    assert page.locator("[data-shell-more-menu] a").evaluate_all(
        "(links) => links.map((link) => link.getAttribute('href'))"
    ) == [href for href, _selector in destinations]

    for href, selector in destinations:
        page.goto(server.base + "/" + href)
        page.wait_for_selector(selector, timeout=WAIT_MS)


def test_agent_and_config_compatibility_routes_have_canonical_homes(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()

    redirects = [
        ("#/chief", "#/agents/chief-of-staff", '[data-screen="agents"]'),
        ("#/workers", "#/config", '[data-screen="config"] [data-workers-list]'),
        (
            "#/workers/coding",
            "#/config/workers/coding",
            '[data-worker-detail][data-worker-id="coding"]',
        ),
        (
            "#/agents/worker-skill",
            "#/config/worker-skill",
            "[data-shared-worker-skill]",
        ),
        (
            "#/agents/workers/coding",
            "#/config/workers/coding",
            '[data-worker-detail][data-worker-id="coding"]',
        ),
    ]

    for legacy, canonical, selector in redirects:
        page.goto(server.base + "/" + legacy)
        page.wait_for_url(f"**/{canonical}", timeout=WAIT_MS)
        page.wait_for_selector(selector, timeout=WAIT_MS)


def test_mobile_tabs_and_short_agents_page_fit_the_dynamic_viewport(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    page.set_viewport_size({"width": 390, "height": 568})
    page.goto(server.base + "/#/agents")
    page.wait_for_selector(
        '[data-screen="agents"] [data-agent-destination]', timeout=WAIT_MS
    )

    tabs = page.locator(".shell-links")
    assert tabs.locator('[data-screen="review"]').is_visible()
    assert tabs.locator('[data-screen="workspace"]').is_visible()
    assert tabs.locator('[data-screen="agents-nav"]').is_visible()
    assert tabs.locator('[data-screen="more"]').is_visible()
    assert page.locator("[data-shell-screen-title]").inner_text() == "Agents"
    status = page.locator("[data-shell-status]")
    assert status.count() == 1
    assert status.get_attribute("role") == "status"
    assert status.get_attribute("aria-label") == "Connected. 0 working."
    assert status.locator("button").count() == 0

    geometry = page.evaluate("""() => ({
          horizontal: document.documentElement.scrollWidth - innerWidth,
          vertical: document.documentElement.scrollHeight - innerHeight,
          navBottom: document.querySelector('.shell-nav').getBoundingClientRect().bottom,
          viewportBottom: innerHeight
        })""")
    assert geometry["horizontal"] <= 0
    assert geometry["vertical"] <= 0
    assert abs(geometry["navBottom"] - geometry["viewportBottom"]) <= 1

    page.locator('[data-screen="more"]').click()
    sheet = page.locator("[data-shell-more-menu]")
    assert sheet.is_visible()
    assert sheet.locator('a[href="#/config"]').is_visible()
    assert sheet.locator('a[href="#/backends"]').is_visible()


def test_shell_status_is_one_quiet_cluster_outside_navigation(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('[data-screen="workspace"]', timeout=WAIT_MS)

    assert page.locator("[data-connection-status]").count() == 1
    status = page.locator("[data-shell-status]")
    assert status.locator("button, a, input, select").count() == 0
    placement = page.evaluate("""() => {
      const status = document.querySelector('[data-shell-status]').getBoundingClientRect();
      const nav = document.querySelector('.shell-nav').getBoundingClientRect();
      return {
        statusTop: status.top,
        statusBottom: status.bottom,
        navTop: nav.top,
        navBottom: nav.bottom,
        insideNavElement: document.querySelector('.shell-nav').contains(
          document.querySelector('[data-shell-status]')
        )
      };
    }""")
    assert placement["statusTop"] >= placement["navTop"]
    assert placement["statusBottom"] <= placement["navBottom"]
    assert placement["insideNavElement"] is False
