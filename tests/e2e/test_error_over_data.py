"""A failed read does not take away a screen that has data.

This needs a real browser against a real server. The claim is about what survives a
failed read: the group the reader opened, and the tickets already drawn under it. Both
live in the DOM the browser owns, and the failure has to be a real aborted request
against a live screen with a live change stream behind it. Neither the frontend unit
tests nor an integration test can hold all three at once.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle


def _refetch_the_board(
    server: ServerHandle, page: Page, api: ApiHelper, ticket: str, title: str
) -> None:
    """Write on the server, then wait until the browser has acted on the change."""
    before = page.evaluate("() => window.__plannerDebug.flushes")
    api.direct_patch(server, f"/api/tickets/{ticket}", {"title": title})
    page.wait_for_function(f"() => window.__plannerDebug.flushes > {before}", timeout=WAIT_MS)


def test_a_failed_read_keeps_the_open_group_and_its_data(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    finished = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Finished thing"
    )["id"]
    other = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Other thing"
    )["id"]
    api.direct_post(server, f"/api/tickets/{finished}/stage", {"to_stage": "done"})

    page = open_page(
        context_factory(), server, "#/workspace?view=tickets", "[data-screen='workspace']"
    )
    done = page.locator('[data-bucket-key="done"]')
    done.wait_for(timeout=WAIT_MS)
    done.locator("> summary").click()
    assert done.get_attribute("open") is not None

    # Every board read fails, including the retry, so the failure reaches the screen.
    page.route("**/api/board", lambda route: route.abort())
    _refetch_the_board(server, page, api, other, "renamed once")
    page.locator("section.board-screen .error-line").wait_for(timeout=WAIT_MS)

    assert done.get_attribute("open") is not None, "the failed read closed the Done group"
    assert "Finished thing" in done.inner_text(), "the failed read took the data away"

    # The next read succeeds.
    page.unroute("**/api/board")
    _refetch_the_board(server, page, api, other, "renamed twice")
    page.locator("section.board-screen .error-line").wait_for(state="detached", timeout=WAIT_MS)

    assert done.get_attribute("open") is not None, "the recovered read closed the Done group"
    assert "Finished thing" in done.inner_text()
