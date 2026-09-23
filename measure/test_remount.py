"""Does an action destroy preview DOM that was already on the screen?

Every preview node is stamped. A stamp that is gone afterwards means the node was
destroyed and built again, which is a new fetch.
"""
from __future__ import annotations
import sqlite3
from collections.abc import Callable
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle
from measure.test_baseline import _seed, _seed_thread, _open

STAMP = """
() => {
  let n = 0;
  for (const el of document.querySelectorAll('[data-file-preview]')) {
    el.dataset.stamp = 'seen-' + (n++);
  }
  return n;
}
"""
SURVIVORS = """
() => {
  const all = document.querySelectorAll('[data-file-preview]');
  const stamped = document.querySelectorAll('[data-file-preview][data-stamp]');
  return [all.length, stamped.length];
}
"""


def _step(page: Page, title: str, before: int) -> None:
    total, survived = page.evaluate(SURVIVORS)
    print(f"  {title}: previews now {total}, of the {before} already on screen "
          f"{survived} survived, {before - survived} were destroyed and rebuilt")


def test_remount(server: ServerHandle, context_factory: Callable[[], BrowserContext],
                 cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Remount probe")["id"]
    paths = _seed(server, ticket_id)
    with sqlite3.connect(server.db_path) as conn:
        _seed_thread(conn, ticket_id, paths)

    page = context_factory().new_page()
    _open(page, server, ticket_id)
    page.wait_for_timeout(5000)
    before = page.evaluate(STAMP)
    print(f"\nstamped {before} previews after the open")

    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(3000)
    _step(page, "after pressing Full", before)

    before = page.evaluate(STAMP)
    page.locator("[data-conversation-expand]").click()
    page.wait_for_timeout(2500)
    _step(page, "after card -> full height", before)

    before = page.evaluate(STAMP)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    bar = page.locator("[data-conversation-rest-bar]")
    if bar.count():
        bar.click(timeout=WAIT_MS)
        page.wait_for_timeout(3000)
        _step(page, "after putting it away and opening it again", before)
    else:
        print("  (no rest bar; state is",
              page.get_attribute("[data-conversation-pane]", "data-conversation-state"), ")")

    before = page.evaluate(STAMP)
    cli(server, "ticket", "create", "--worker-type", "coding", "--title", "Noise")
    page.wait_for_timeout(3000)
    _step(page, "after an unrelated write lands", before)

    before = page.evaluate(STAMP)
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(1500)
    page.locator("[data-conversation-lens-toggle]").click()
    page.wait_for_timeout(3000)
    _step(page, "after Full -> Focus -> Full", before)
